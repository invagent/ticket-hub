"""KSM outbound writeback sender (D4 第②段) — drains sync_outbox → KSM.

The cascade producers (reply_sync / status_cascade) enqueue one sync_outbox
row per affected SOURCED ticket; this is the KSM-side consumer. It maps each
pending row to the right KSM mutation and drives the lock → (refresh) →
handle/supply sequence the KSM workflow requires.

Outbox → KSM mapping
--------------------
    kind='reply'                 → lock → refresh → handleKsmOrder(is_deal=True)
                                   ("答复关单" — answer the customer + close)
    kind='status' in_progress    → lockKsmOrder                ("接管受理")
    kind='status' released       → lock → refresh → handleKsmOrder(is_deal=True)
                                   (close with the hub reply, else a default note)
    kind='supply'                → lock → refresh → supplyKsmOrder   ("补料")

Why lock → refresh → handle
---------------------------
KSM requires the order be 接管(locked) by our handler before it can be
handled, and `handleKsmOrder.currentNodeID` must be the node *after* lock
(the workflow advances on lock). So we lock first, then re-pull the latest
detail via subscribeCallback to read the fresh node/product/version/module
ids. The notice needed for that re-pull comes from the Redis NoticeStore
(24h TTL); if it's gone we fall back to the ids captured at ingest time and
let KSM reject a stale node — never a silent success.

Safety
------
* Switch-gated: `ksm_writeback_enabled` (default off) skips entirely.
* `ksm_writeback_dry_run` (default on): assemble the request, log it, mark the
  row 'skipped' — nothing hits KSM until BOTH switches are flipped.
* Any KSM error increments attempts + records last_error; after
  `ksm_writeback_max_attempts` the row is 'failed' (转人工), never retried
  silently, never marked sent.
* Idempotent: only 'pending' rows are drained; success flips them to 'sent'.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.ksm import (
    HandleOrderRequest,
    KSMClient,
    KSMConfig,
    KSMError,
    LockOrderRequest,
    ReturnOrderRequest,
    SupplyOrderRequest,
)
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.op_status import OP_CLOSED, apply_op_status
from app.services.ksm.notice_store import NoticeStoreLike

if TYPE_CHECKING:
    from app.services.cascade.outbox_retry import OutboxRetryResult

logger = get_logger(__name__)

_DEFAULT_RELEASED_NOTE = "您反馈的问题已处理完成，如仍有疑问欢迎继续反馈。"

# 关单类 action 真发成功后，本地 ticket→closed / hub→resolved（与智齿写回一致）。
_CLOSING_ACTIONS = frozenset({"reply", "release_note", "close"})
_TICKET_TERMINAL_STATUSES = frozenset({"done", "closed", "rejected", "superseded"})
# lock errors that mean "already taken over" — benign, proceed to handle
_ALREADY_LOCKED_HINTS = ("已被接管", "已接管", "已锁定", "重复接管")


@dataclass(slots=True)
class DrainReport:
    scanned: int = 0
    sent: int = 0
    skipped: int = 0  # dry-run or non-actionable
    failed: int = 0  # exhausted retries
    deferred: int = 0  # transient error, will retry next pass
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class _KSMFields:
    """The KSM ids a writeback needs, pulled from a ticket's source_payload."""

    bill_id: str
    node_id: str
    product_id: str
    version_id: str
    module_id: str
    back_type: str
    linkman: str
    email: str
    mobile: str
    opercache_id: str = ""


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _return_target_opercache_id(raw: dict[str, Any]) -> str:
    """退回目标「受理」节点的操作缓存 id（returnKsmOrder 的 opercacheID）。

    returnKsmOrder 的 opercacheID 决定退回**目标节点**，currentNodeID 是源节点
    （见 _return 方法）。退回必须落到「受理」节点，否则工单退回后仍停在协同处理
    态。handleSteps 里可能有多条「受理」记录（工单反复退回再受理），取
    handleDateTime 最新（离现在最近）那条的 opercacheId。找不到回落空串。
    """
    steps = raw.get("handleSteps")
    if not isinstance(steps, list):
        return ""
    accept = [
        h
        for h in steps
        if isinstance(h, dict) and h.get("nodeName") == "受理" and h.get("opercacheId")
    ]
    if not accept:
        return ""
    # handleDateTime 是 "YYYY-MM-DD HH:MM:SS" 字符串，字典序 == 时间序，reverse 取最新。
    accept.sort(key=lambda h: _s(h.get("handleDateTime")), reverse=True)
    return _s(accept[0].get("opercacheId"))


def _extract_ksm_fields(
    source_payload: dict[str, Any] | None, *, fallback_bill_id: str = ""
) -> _KSMFields:
    """Read KSM writeback ids from a ticket.source_payload.

    The ingester stores the mapped payload with the raw subscribeCallback
    `data` block under `_subscribe_callback`; the ids we need (node/product/
    version/module/feedbackType + customer contact) live in that raw block.
    bill_id falls back to the ticket's source_ticket_id (== KSM billId).
    """
    payload = source_payload or {}
    raw = payload.get("_subscribe_callback")
    raw = raw if isinstance(raw, dict) else {}

    def _nested_id(key: str) -> str:
        obj = raw.get(key)
        return _s(obj.get("id")) if isinstance(obj, dict) else ""

    customer = raw.get("customerInfo")
    customer = customer if isinstance(customer, dict) else {}

    bill_id = _s(payload.get("billId") or raw.get("billId") or raw.get("id") or fallback_bill_id)
    node_id = _nested_id("node")
    return _KSMFields(
        bill_id=bill_id,
        node_id=node_id,
        product_id=_nested_id("product"),
        version_id=_nested_id("version"),
        module_id=_nested_id("module"),
        back_type=_s(raw.get("feedbackType")),
        linkman=_s(customer.get("linkman") or payload.get("accountName")),
        email=_s(customer.get("email") or payload.get("email")),
        mobile=_s(customer.get("mobile") or payload.get("mobile")),
        opercache_id=_return_target_opercache_id(raw),
    )


def _merge_refreshed(base: _KSMFields, detail: dict[str, Any]) -> _KSMFields:
    """Overlay freshly-pulled ids onto base (only where the refresh has them)."""

    def _nested_id(key: str) -> str:
        obj = detail.get(key)
        return _s(obj.get("id")) if isinstance(obj, dict) else ""

    customer = detail.get("customerInfo")
    customer = customer if isinstance(customer, dict) else {}
    node_id = _nested_id("node") or base.node_id
    return _KSMFields(
        bill_id=base.bill_id,
        node_id=node_id,
        product_id=_nested_id("product") or base.product_id,
        version_id=_nested_id("version") or base.version_id,
        module_id=_nested_id("module") or base.module_id,
        back_type=_s(detail.get("feedbackType")) or base.back_type,
        linkman=_s(customer.get("linkman")) or base.linkman,
        email=_s(customer.get("email")) or base.email,
        mobile=_s(customer.get("mobile")) or base.mobile,
        opercache_id=_return_target_opercache_id(detail) or base.opercache_id,
    )


class KSMWritebackSender:
    """Drains pending KSM sync_outbox rows. Holds one KSM client for the pass."""

    def __init__(
        self,
        db: Session,
        *,
        client: KSMClient,
        settings: Settings,
        notice_store: NoticeStoreLike | None = None,
    ) -> None:
        self._db = db
        self._client = client
        self._settings = settings
        self._notice_store = notice_store

    # ---- public --------------------------------------------------------

    def drain(self) -> DrainReport:
        """One drain pass over pending KSM rows. Commits per-row."""
        report = DrainReport()
        rows = list(
            self._db.execute(
                select(SyncOutbox)
                .where(
                    SyncOutbox.target_source_code == "ksm",
                    SyncOutbox.status == "pending",
                )
                .order_by(SyncOutbox.created_at.asc())
                .limit(self._settings.ksm_writeback_batch)
            )
            .scalars()
            .all()
        )
        report.scanned = len(rows)
        for row in rows:
            self._process_row(row, report)
        return report

    # ---- per-row -------------------------------------------------------

    def _process_row(self, row: SyncOutbox, report: DrainReport) -> None:
        ticket = self._db.get(Ticket, row.ticket_id)
        if ticket is None:
            self._mark_skipped(row, "ticket not found")
            report.skipped += 1
            return

        fields = _extract_ksm_fields(
            ticket.source_payload, fallback_bill_id=ticket.source_ticket_id or ""
        )
        if not fields.bill_id:
            self._mark_skipped(row, "no billId in source_payload")
            report.skipped += 1
            return

        # 退回：优先用 takeover 时持久化的「受理节点 opercacheId + 当前节点」（迁移 0038）。
        # notice 24h 过期后 _refresh 拿不到实时详情，用旧快照会报「已流转至其他节点」。
        if row.kind == "return":
            fields = replace(
                fields,
                opercache_id=ticket.ksm_accept_opercache_id or fields.opercache_id,
                node_id=ticket.ksm_current_node_id or fields.node_id,
            )

        action = self._resolve_action(row)
        if action is None:
            self._mark_skipped(row, f"no KSM action for kind={row.kind} payload={row.payload}")
            report.skipped += 1
            return

        if self._settings.ksm_writeback_dry_run:
            self._mark_skipped(row, f"dry_run: would {action} bill={fields.bill_id}")
            report.skipped += 1
            return

        try:
            self._execute(action, row, fields, persisted_node_id=ticket.ksm_current_node_id)
        except KSMError as e:
            self._record_failure(row, report, str(e))
            return
        except Exception as e:
            self._record_failure(row, report, f"unexpected: {e}")
            logger.exception("ksm_writeback_unexpected", outbox_id=row.id)
            return

        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        row.attempts += 1
        # 关单类 action 真发成功后，本地工单/hub 推终态，与 KSM 侧一致。
        # 补料/接管/进度通知不关单，不动状态（supplementing 由主管线下收集补料时置，与此处无关）。
        if action in _CLOSING_ACTIONS:
            self._close_local(row, ticket)
        # 补料真发成功 → 清接管状态回未接管（工单交还提单人，下次再进来需重新接管）。
        # 入库即接管的生命周期闭环，见 services/ksm/takeover.py。
        if action == "supply":
            ticket.ksm_takeover_status = None
            ticket.ksm_takeover_error = None
        # 退回真发成功 → 工单交还 KSM 重新分派：清接管状态 + 本地工单关闭（不再跟踪）。
        # 不碰 hub（退回 ≠ 答复关单；hub 由主管见工单关闭后手动处理）。
        if action == "return":
            ticket.ksm_takeover_status = None
            ticket.ksm_takeover_error = None
            self._close_ticket_returned(row, ticket)
        self._db.commit()
        report.sent += 1
        logger.info("ksm_writeback_sent", outbox_id=row.id, action=action, bill_id=fields.bill_id)

    def _close_local(self, row: SyncOutbox, ticket: Ticket) -> None:
        """关单真发成功后：ticket→closed、hub→resolved，记 status_history。
        已在终态的不重置（幂等 + 保护投诉 closed 等）。不 commit（随外层）。"""
        history = StatusHistoryRepository(self._db)
        changed_by = "system:ksm_writeback"
        if ticket.status not in _TICKET_TERMINAL_STATUSES:
            prev = ticket.status
            ticket.status = "closed"
            history.record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=prev,
                to_status="closed",
                changed_by=changed_by,
                reason=f"KSM 答复关单回写成功（outbox={row.id}, kind={row.kind}）",
            )
        hub = self._db.get(HubIssue, row.hub_issue_id) if row.hub_issue_id else None
        if hub is not None and hub.status != "resolved":
            hub_prev = hub.status
            hub.status = "resolved"
            history.record(
                entity_type="hub_issue",
                entity_id=hub.id,
                from_status=hub_prev,
                to_status="resolved",
                changed_by=changed_by,
                reason=f"Operation 答复关单回写成功（outbox={row.id}）",
            )
        # op_status 业务层不在此处推进到 closed（此前的实现在这里直接把
        # answered→closed，等于「答复回写 KSM 成功」= 关单，导致 T+7 beat
        # （close_overdue_answered）从未真正生效——KSM 的 handleKsmOrder(isDeal=2)
        # 只是 KSM 侧唯一的流转推进机制，不代表客户已确认解决。op_status 停在
        # answered，唯一能推进到 closed 的路径是 T+7 beat：答复后 N 天内没有
        # 客户重推同一单驳回（ksm_ingester 会转回 processing）才自动关闭。

    def _close_ticket_returned(self, row: SyncOutbox, ticket: Ticket) -> None:
        """退回真发成功后：本地工单 → closed（交还 KSM 重新分派，不再跟踪）。

        工单关闭 + 若所挂 Operation hub 已无其它未终态工单，则同步关 op_status
        （否则任务表「处理状态」一直停留在 processing）。不碰研发类 hub（研发走
        Linear，退回是 Operation 场景）。已在终态的不重置（幂等）。不 commit。
        """
        if ticket.status not in _TICKET_TERMINAL_STATUSES:
            prev = ticket.status
            ticket.status = "closed"
            StatusHistoryRepository(self._db).record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=prev,
                to_status="closed",
                changed_by="system:ksm_writeback",
                reason=f"退回 KSM 重新分派成功（outbox={row.id}, kind=return）",
            )
        # 所挂 Operation hub：仅当无其它仍活跃的关联工单时，关闭 op_status。
        hub = self._db.get(HubIssue, row.hub_issue_id) if row.hub_issue_id else None
        if hub is None or hub.type != "Operation":
            return
        active = (
            self._db.query(Ticket.id)
            .filter(
                Ticket.hub_issue_id == hub.id,
                Ticket.deleted_at.is_(None),
                Ticket.status.notin_(list(_TICKET_TERMINAL_STATUSES)),
            )
            .first()
        )
        if active is None and hub.op_status != OP_CLOSED:
            apply_op_status(
                self._db,
                hub,
                to_status=OP_CLOSED,
                handler=hub.op_handler or "agent",
                reason=f"KSM 退回成功，工单已全部关闭（outbox={row.id}）",
            )

    def _resolve_action(self, row: SyncOutbox) -> str | None:
        """Map an outbox row to: 'reply' | 'lock' | 'close' | 'supply' | 'return'
        | 'release_note'（关单）| 'progress_note'（不关单）."""
        if row.kind == "reply":
            return "reply"
        if row.kind == "release_note":
            return "release_note"
        if row.kind == "progress_note":
            return "progress_note"
        if row.kind == "supply":
            return "supply"
        if row.kind == "return":
            return "return"
        if row.kind == "status":
            to_status = (row.payload or {}).get("to_status")
            if to_status == "in_progress":
                return "lock"
            if to_status == "released":
                return "close"
        return None

    def _execute(
        self, action: str, row: SyncOutbox, fields: _KSMFields, *, persisted_node_id: str | None = None
    ) -> None:
        if action == "lock":
            self._lock(fields)
            return
        # 退回：不先 lock（lock 是「接管」语义，与退回相悖，实测未 lock 直接 return 成功）。
        # 只需重拉最新节点 + 对应 opercacheId，否则旧节点会报「已流转至其他节点」。
        if action == "return":
            fresh = self._refresh(fields)
            self._return(fresh, _s((row.payload or {}).get("deal_opinion")).strip())
            return
        # all remaining actions need a fresh node → lock then refresh
        self._lock(fields)
        fresh = self._refresh(fields)
        # notice 24h 过期时 _refresh 回落入库快照节点（比 takeover 节点旧），
        # KSM 报「已流转至其他节点」。refresh 回落的标志是 node_id 与入库快照相同。
        # ksm_current_node_id 是 takeover handle 后持久化的「协同处理」节点，比入库
        # 快照新——refresh 失败时用它替换，refresh 成功时保留 refresh 结果不覆盖。
        if persisted_node_id and fresh.node_id == fields.node_id:
            fresh = replace(fresh, node_id=persisted_node_id)
        if action == "reply":
            self._handle_close(fresh, self._reply_text(row))
        elif action == "release_note":
            # 发版通知（研发协同）：文案在 payload.note，同 reply 走答复关单
            self._handle_close(fresh, _s((row.payload or {}).get("note")).strip())
        elif action == "progress_note":
            # owner-split 进度通知（ADR-0016 P4）：x<n 只回复不关单
            # （is_deal=False —— 第 1 条通知就关掉客户单是 review 抓出的坑）
            self._handle_progress(fresh, _s((row.payload or {}).get("note")).strip())
        elif action == "close":
            self._handle_close(fresh, self._released_text(row))
        elif action == "supply":
            self._supply(fresh, self._supply_text(row))

    # ---- KSM ops -------------------------------------------------------

    def _lock(self, fields: _KSMFields) -> None:
        try:
            self._client.lock_order(
                LockOrderRequest(
                    account=self._settings.ksm_handler_name,
                    account_name=self._settings.ksm_handler_name,
                    account_number=self._settings.ksm_handler_number,
                    bill_id=fields.bill_id,
                )
            )
        except KSMError as e:
            if any(h in str(e) for h in _ALREADY_LOCKED_HINTS):
                logger.info("ksm_already_locked", bill_id=fields.bill_id)
                return
            raise

    def _handle_close(self, fields: _KSMFields, reply: str) -> None:
        self._client.handle_order(
            HandleOrderRequest(
                account=self._settings.ksm_handler_name,
                account_name=self._settings.ksm_handler_name,
                account_number=self._settings.ksm_handler_number,
                bill_id=fields.bill_id,
                linkman=fields.linkman,
                customer_email=fields.email,
                customer_mobile=fields.mobile,
                product_id=fields.product_id,
                version_id=fields.version_id,
                module_id=fields.module_id,
                back_type=fields.back_type,
                node_id=fields.node_id,
                deal_opinion=reply,
                is_deal=True,
            )
        )

    def _handle_progress(self, fields: _KSMFields, note: str) -> None:
        """回复不关单（is_deal=False）——owner-split x/n 进度通知。"""
        self._client.handle_order(
            HandleOrderRequest(
                account=self._settings.ksm_handler_name,
                account_name=self._settings.ksm_handler_name,
                account_number=self._settings.ksm_handler_number,
                bill_id=fields.bill_id,
                linkman=fields.linkman,
                customer_email=fields.email,
                customer_mobile=fields.mobile,
                product_id=fields.product_id,
                version_id=fields.version_id,
                module_id=fields.module_id,
                back_type=fields.back_type,
                node_id=fields.node_id,
                deal_opinion=note,
                is_deal=False,
            )
        )

    def _supply(self, fields: _KSMFields, note: str) -> None:
        self._client.supply_order(
            SupplyOrderRequest(
                account=self._settings.ksm_handler_name,
                account_name=self._settings.ksm_handler_name,
                account_number=self._settings.ksm_handler_number,
                bill_id=fields.bill_id,
                node_id=fields.node_id,
                deal_opinion=note[:4000],
            )
        )

    def _return(self, fields: _KSMFields, deal_opinion: str) -> None:
        """退回 KSM（returnKsmOrder）——转错模块打回重新分派，不关单。

        current_node_id（源节点）= refresh 后的最新 node.id；opercache_id（退回目标）
        = 「受理」节点时间最新那条 handleStep 的 opercacheId。这样工单退回后落到
        受理节点，而不是原地停在协同处理态。二者都必须 refresh 后取最新，否则 KSM
        报「已流转至其他节点」。
        """
        self._client.return_order(
            ReturnOrderRequest(
                account=self._settings.ksm_handler_name,
                account_name=self._settings.ksm_handler_name,
                account_number=self._settings.ksm_handler_number,
                bill_id=fields.bill_id,
                deal_opinion=deal_opinion[:4000],
                opercache_id=fields.opercache_id,
                current_node_id=fields.node_id,
            )
        )

    def _refresh(self, fields: _KSMFields) -> _KSMFields:
        """Best-effort: re-pull latest detail to refresh node id post-lock.

        Falls back to the ingest-time ids when no notice is cached or the
        pull fails (handle will then surface a KSM error if the node is stale
        — never silent)."""
        if self._notice_store is None:
            return fields
        notice = self._notice_store.get(fields.bill_id)
        if notice is None:
            logger.info("ksm_refresh_no_notice", bill_id=fields.bill_id)
            return fields
        try:
            detail = self._client.get_order_detail(
                bill_id=fields.bill_id,
                notice_num=notice.notice_num,
                subscribe_num=notice.subscribe_num,
            )
        except KSMError as e:
            logger.warning("ksm_refresh_failed", bill_id=fields.bill_id, error=str(e))
            return fields
        return _merge_refreshed(fields, detail)

    # ---- text builders -------------------------------------------------

    def _reply_text(self, row: SyncOutbox) -> str:
        return _s((row.payload or {}).get("reply_content")).strip()

    def _supply_text(self, row: SyncOutbox) -> str:
        return _s((row.payload or {}).get("supply_note")).strip()

    def _released_text(self, row: SyncOutbox) -> str:
        hub = self._db.get(HubIssue, row.hub_issue_id)
        if hub is not None and hub.reply_content and not hub.reply_is_draft:
            return str(hub.reply_content).strip()
        return _DEFAULT_RELEASED_NOTE

    # ---- bookkeeping ---------------------------------------------------

    def _mark_skipped(self, row: SyncOutbox, reason: str) -> None:
        row.status = "skipped"
        row.last_error = reason[:1000]
        self._db.commit()
        logger.info("ksm_writeback_skipped", outbox_id=row.id, reason=reason)

    def _record_failure(self, row: SyncOutbox, report: DrainReport, error: str) -> None:
        row.attempts += 1
        row.last_error = error[:1000]
        if row.attempts >= self._settings.ksm_writeback_max_attempts:
            row.status = "failed"
            report.failed += 1
            logger.warning(
                "ksm_writeback_failed", outbox_id=row.id, attempts=row.attempts, error=error
            )
        else:
            # stays 'pending' → retried next pass
            report.deferred += 1
            logger.info(
                "ksm_writeback_deferred", outbox_id=row.id, attempts=row.attempts, error=error
            )
        report.errors.append(f"outbox={row.id}: {error}")
        self._db.commit()


def drain_ksm_outbox(
    db: Session,
    *,
    client: KSMClient | None = None,
    notice_store: NoticeStoreLike | None = None,
    settings: Settings | None = None,
) -> DrainReport:
    """Entry point. Builds a KSM client from settings if not injected.

    Returns an empty report (and touches nothing) when the writeback switch
    is off or the handler identity is unconfigured — same skip-quietly
    posture as the Linear poller."""
    settings = settings or get_settings()
    report = DrainReport()
    if not settings.ksm_writeback_enabled:
        logger.info("ksm_writeback_disabled")
        return report
    if not settings.ksm_handler_name or not settings.ksm_handler_number:
        logger.warning("ksm_writeback_no_handler_identity")
        return report

    owns_client = client is None
    if client is None:
        client = KSMClient(KSMConfig.from_settings(settings))
    try:
        sender = KSMWritebackSender(db, client=client, settings=settings, notice_store=notice_store)
        return sender.drain()
    finally:
        if owns_client:
            client.close()


def retry_single_row(sender: KSMWritebackSender, row: SyncOutbox) -> OutboxRetryResult:
    """处理人手工重试单行的适配层：复用 _process_row，不重置 attempts/status
    ——该行已是 failed，_process_row 内部的 _record_failure 若这次仍失败会把
    attempts 再 +1（通常维持 failed，因为早已 >= max_attempts）；成功则正常
    翻 sent。只关心这一行最终落地的 status/last_error，不复用 DrainReport 的
    批量聚合字段。运行期延迟导入避免循环依赖（outbox_retry.py 反向 import
    本模块），类型标注走 TYPE_CHECKING 避免 mypy 报 no-any-return。"""
    from app.services.cascade.outbox_retry import OutboxRetryResult

    report = DrainReport()
    sender._process_row(row, report)
    return OutboxRetryResult(outbox_id=row.id, sent=row.status == "sent", error=row.last_error)
