"""智齿出站回写 sender — drain sync_outbox（target_source_code='zhichi'）→ 智齿.

cascade 生产者（reply_sync / status_cascade / supply_sync / owner_split）已为
每个有源智齿工单入队 sync_outbox 行；这里是智齿侧消费端。镜像 KSM writeback，
但更简单：智齿一个 reply_ticket（save_ticket_reply）搞定，无 KSM 的
lock→refresh→handle 时序、无 NoticeStore 重拉。

kind → ticket_status 映射
-------------------------
    reply / release_note / status(released)  → '3'（已解决，答复关单）
    supply / progress_note                   → '2'（等待回复，不关单）
    status(in_progress)                      → skip（智齿无"接管"概念）

坐席
----
回复必须带 reply_agentid（智齿契约）。坐席名取 ticket.source_payload.raw
的 deal_agent_name；为空则用 settings.zhichi_fallback_agent_name（默认"莉莉"）。
经 client.get_agent_by_name 查 agentid；查不到（连兜底坐席都没有）→ 记 failure
转人工，绝不静默跳过。

灰度（同 KSM）
--------------
* zhichi_writeback_enabled（默认 False）→ drain 直接返回空报告。
* zhichi_writeback_dry_run（默认 True）→ 组装 + log + 标 skipped，不真发。
* 失败 attempts++；超 zhichi_writeback_max_attempts 标 failed（转人工），否则
  留 pending 下轮重试。仅 pending 被 drain；成功翻 sent 幂等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.zhichi import ReplyTicketRequest, ZhichiClient, ZhichiConfig, ZhichiError
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket

logger = get_logger(__name__)

_DEFAULT_RELEASED_NOTE = "您反馈的问题已处理完成，如仍有疑问欢迎继续反馈。"


@dataclass(slots=True)
class DrainReport:
    scanned: int = 0
    sent: int = 0
    skipped: int = 0  # dry-run 或非可处理
    failed: int = 0  # 耗尽重试
    deferred: int = 0  # 瞬时错误，下轮重试
    errors: list[str] = field(default_factory=list)


def _s(v: Any) -> str:
    return "" if v is None else str(v)


class ZhichiWritebackSender:
    """Drains pending 智齿 sync_outbox rows. 持有一个 client 跑完整轮。"""

    def __init__(self, db: Session, *, client: Any, settings: Settings) -> None:
        self._db = db
        self._client = client
        self._settings = settings

    def drain(self) -> DrainReport:
        report = DrainReport()
        rows = list(
            self._db.execute(
                select(SyncOutbox)
                .where(
                    SyncOutbox.target_source_code == "zhichi",
                    SyncOutbox.status == "pending",
                )
                .order_by(SyncOutbox.created_at.asc())
                .limit(self._settings.zhichi_writeback_batch)
            )
            .scalars()
            .all()
        )
        report.scanned = len(rows)
        for row in rows:
            self._process_row(row, report)
        return report

    def _process_row(self, row: SyncOutbox, report: DrainReport) -> None:
        ticket = self._db.get(Ticket, row.ticket_id)
        if ticket is None:
            self._mark_skipped(row, "ticket not found")
            report.skipped += 1
            return

        status_code = self._resolve_status(row)
        if status_code is None:
            self._mark_skipped(row, f"no zhichi action for kind={row.kind} payload={row.payload}")
            report.skipped += 1
            return

        if self._settings.zhichi_writeback_dry_run:
            self._mark_skipped(
                row, f"dry_run: would reply ticket={ticket.source_ticket_id} status={status_code}"
            )
            report.skipped += 1
            return

        try:
            self._reply(row, ticket, status_code)
        except ZhichiError as e:
            self._record_failure(row, report, str(e))
            return
        except Exception as e:
            self._record_failure(row, report, f"unexpected: {e}")
            logger.exception("zhichi_writeback_unexpected", outbox_id=row.id)
            return

        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        row.attempts += 1
        self._db.commit()
        report.sent += 1
        logger.info(
            "zhichi_writeback_sent", outbox_id=row.id, ticket_id=ticket.id, status=status_code
        )

    def _resolve_status(self, row: SyncOutbox) -> str | None:
        """kind → ticket_status，或 None（skip）。"""
        if row.kind in ("reply", "release_note"):
            return "3"
        if row.kind in ("supply", "progress_note"):
            return "2"
        if row.kind == "status":
            to_status = (row.payload or {}).get("to_status")
            if to_status == "released":
                return "3"
            # in_progress 等 → skip（智齿无接管概念）
        return None

    def _reply(self, row: SyncOutbox, ticket: Ticket, status_code: str) -> None:
        raw = (ticket.source_payload or {}).get("raw") or {}
        agent_name = _s(raw.get("deal_agent_name")) or self._settings.zhichi_fallback_agent_name
        agent = self._client.get_agent_by_name(agent_name)
        if agent is None:
            raise ZhichiError(f"坐席 {agent_name!r} 在智齿 agent_list 中查无 agentid")
        req = ReplyTicketRequest(
            ticket_id=_s(ticket.source_ticket_id),
            ticket_title=_s(raw.get("ticket_title") or ticket.title),
            ticket_content=_s(raw.get("ticket_content") or ticket.body),
            ticket_status=status_code,
            ticket_level=_s(raw.get("ticket_level")) or "1",
            reply_agentid=agent.agentid,
            reply_agent_name=agent.agent_name,
            reply_content=self._reply_text(row),
        )
        self._client.reply_ticket(req)

    def _reply_text(self, row: SyncOutbox) -> str:
        p = row.payload or {}
        if row.kind == "reply":
            return _s(p.get("reply_content")).strip()
        if row.kind == "supply":
            return _s(p.get("supply_note")).strip()
        if row.kind in ("release_note", "progress_note"):
            return _s(p.get("note")).strip()
        if row.kind == "status":
            hub = self._db.get(HubIssue, row.hub_issue_id)
            if hub is not None and hub.reply_content:
                return str(hub.reply_content).strip()
            return _DEFAULT_RELEASED_NOTE
        return ""

    def _mark_skipped(self, row: SyncOutbox, reason: str) -> None:
        row.status = "skipped"
        row.last_error = reason[:1000]
        self._db.commit()
        logger.info("zhichi_writeback_skipped", outbox_id=row.id, reason=reason)

    def _record_failure(self, row: SyncOutbox, report: DrainReport, error: str) -> None:
        row.attempts += 1
        row.last_error = error[:1000]
        if row.attempts >= self._settings.zhichi_writeback_max_attempts:
            row.status = "failed"
            report.failed += 1
            logger.warning(
                "zhichi_writeback_failed", outbox_id=row.id, attempts=row.attempts, error=error
            )
        else:
            report.deferred += 1
            logger.info(
                "zhichi_writeback_deferred", outbox_id=row.id, attempts=row.attempts, error=error
            )
        report.errors.append(f"outbox={row.id}: {error}")
        self._db.commit()


def drain_zhichi_outbox(
    db: Session,
    *,
    client: Any | None = None,
    settings: Settings | None = None,
) -> DrainReport:
    """入口。构造 client（未注入时），enabled 关或凭证缺失时空转返回。"""
    settings = settings or get_settings()
    report = DrainReport()
    if not settings.zhichi_writeback_enabled:
        logger.info("zhichi_writeback_disabled")
        return report
    if not settings.zhichi_appid or not settings.zhichi_app_key:
        logger.warning("zhichi_writeback_no_credentials")
        return report

    owns_client = client is None
    if client is None:
        client = ZhichiClient(ZhichiConfig.from_settings(settings))
    try:
        return ZhichiWritebackSender(db, client=client, settings=settings).drain()
    finally:
        if owns_client:
            client.close()
