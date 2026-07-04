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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.ksm import (
    HandleOrderRequest,
    KSMClient,
    KSMConfig,
    KSMError,
    LockOrderRequest,
    SupplyOrderRequest,
)
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket
from app.services.ksm.notice_store import NoticeStoreLike

logger = get_logger(__name__)

_DEFAULT_RELEASED_NOTE = "您反馈的问题已处理完成，如仍有疑问欢迎继续反馈。"
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


def _s(v: Any) -> str:
    return "" if v is None else str(v)


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
    return _KSMFields(
        bill_id=bill_id,
        node_id=_nested_id("node"),
        product_id=_nested_id("product"),
        version_id=_nested_id("version"),
        module_id=_nested_id("module"),
        back_type=_s(raw.get("feedbackType")),
        linkman=_s(customer.get("linkman") or payload.get("accountName")),
        email=_s(customer.get("email") or payload.get("email")),
        mobile=_s(customer.get("mobile") or payload.get("mobile")),
    )


def _merge_refreshed(base: _KSMFields, detail: dict[str, Any]) -> _KSMFields:
    """Overlay freshly-pulled ids onto base (only where the refresh has them)."""

    def _nested_id(key: str) -> str:
        obj = detail.get(key)
        return _s(obj.get("id")) if isinstance(obj, dict) else ""

    customer = detail.get("customerInfo")
    customer = customer if isinstance(customer, dict) else {}
    return _KSMFields(
        bill_id=base.bill_id,
        node_id=_nested_id("node") or base.node_id,
        product_id=_nested_id("product") or base.product_id,
        version_id=_nested_id("version") or base.version_id,
        module_id=_nested_id("module") or base.module_id,
        back_type=_s(detail.get("feedbackType")) or base.back_type,
        linkman=_s(customer.get("linkman")) or base.linkman,
        email=_s(customer.get("email")) or base.email,
        mobile=_s(customer.get("mobile")) or base.mobile,
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
            self._execute(action, row, fields)
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
        self._db.commit()
        report.sent += 1
        logger.info("ksm_writeback_sent", outbox_id=row.id, action=action, bill_id=fields.bill_id)

    def _resolve_action(self, row: SyncOutbox) -> str | None:
        """Map an outbox row to one of: 'reply' | 'lock' | 'close' | 'supply'."""
        if row.kind == "reply":
            return "reply"
        if row.kind == "release_note":
            return "release_note"
        if row.kind == "supply":
            return "supply"
        if row.kind == "status":
            to_status = (row.payload or {}).get("to_status")
            if to_status == "in_progress":
                return "lock"
            if to_status == "released":
                return "close"
        return None

    def _execute(self, action: str, row: SyncOutbox, fields: _KSMFields) -> None:
        if action == "lock":
            self._lock(fields)
            return
        # all remaining actions need a fresh node → lock then refresh
        self._lock(fields)
        fresh = self._refresh(fields)
        if action == "reply":
            self._handle_close(fresh, self._reply_text(row))
        elif action == "release_note":
            # 发版通知（研发协同）：文案在 payload.note，同 reply 走答复关单
            self._handle_close(fresh, _s((row.payload or {}).get("note")).strip())
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
        if hub is not None and hub.reply_content:
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
