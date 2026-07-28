"""Reply sync (决策 15, D4 第②段) — author an Operation reply and fan out.

One reply lives on the hub_issue (versioned, hub_issue_reply_history) and
cascades to every linked ticket:

    ticket.cached_reply_content / cached_reply_version   — 站内缓存，列表页直读
    sync_outbox (kind='reply')                            — 每个有源工单一行，
                                                            D5 sender 负责真正
                                                            回写 KSM/智齿

Child tickets (split 产物, source_code NULL) get the cache but no outbox row —
there is no source system to write back to.

Authoring is restricted to Operation hub_issues (ck_hub_issues_operation_fields
enforces the same at the DB layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue, HubIssueReplyHistory, SyncOutbox, Ticket

logger = get_logger(__name__)


class ReplySyncError(Exception):
    """Reply can't be authored; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class ReplyResult:
    hub_issue_id: int
    version: int
    cascaded_ticket_ids: list[int]
    outbox_ids: list[int]


def author_reply(
    db: Session,
    hub_issue_id: int,
    *,
    content: str,
    authored_by: str,
) -> ReplyResult:
    """Author/replace the Operation reply, cascade, enqueue outbox. Commits."""
    content = (content or "").strip()
    if not content:
        raise ReplySyncError("reply content is empty")

    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise ReplySyncError(f"hub_issue {hub_issue_id} not found")
    if hub.type != "Operation":
        raise ReplySyncError(
            f"hub_issue {hub.short_code} is type={hub.type!r} — replies are Operation-only"
        )

    now = datetime.now(UTC)
    version = hub.reply_content_version + 1
    hub.reply_content = content
    hub.reply_content_version = version
    hub.reply_authored_by = authored_by
    hub.reply_updated_at = now
    db.add(
        HubIssueReplyHistory(
            hub_issue_id=hub.id,
            version=version,
            reply_content=content,
            authored_by=authored_by,
        )
    )

    tickets = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    cascaded: list[int] = []
    outbox_ids: list[int] = []
    for t in tickets:
        t.cached_reply_content = content
        t.cached_reply_version = version
        cascaded.append(t.id)
        if t.source_code and t.source_ticket_id:
            row = SyncOutbox(
                kind="reply",
                target_source_code=t.source_code,
                ticket_id=t.id,
                source_ticket_id=t.source_ticket_id,
                hub_issue_id=hub.id,
                payload={
                    "reply_content": content,
                    "reply_version": version,
                    "authored_by": authored_by,
                    "hub_short_code": hub.short_code,
                },
            )
            db.add(row)
            db.flush()
            outbox_ids.append(row.id)

    db.commit()
    logger.info(
        "reply_authored",
        hub_issue_id=hub.id,
        version=version,
        cascaded=len(cascaded),
        outbox=len(outbox_ids),
        authored_by=authored_by,
    )
    return ReplyResult(
        hub_issue_id=hub.id,
        version=version,
        cascaded_ticket_ids=cascaded,
        outbox_ids=outbox_ids,
    )


def backfill_reply_to_ticket(db: Session, hub: HubIssue, ticket: Ticket) -> bool:
    """给「后挂进已答复 hub」的新 ticket 补发答复：回填缓存 + 入 reply outbox。

    hub_dedup 合并把新 ticket 挂到一个已答复的 Operation hub 时，author_reply
    早已跑完、只覆盖了当时在库的 ticket——新挂进来的会漏掉答复，第二个客户收
    不到回复。合并路径调本函数补上。

    仅在 hub 已有答复（reply_content_version>=1）时生效。有源 ticket 才入 outbox
    （Child 无源系统，只回填缓存）。不 commit（随调用方事务）。返回 True=已补发。
    """
    if hub.reply_content_version < 1 or not hub.reply_content:
        return False

    ticket.cached_reply_content = hub.reply_content
    ticket.cached_reply_version = hub.reply_content_version
    if ticket.source_code and ticket.source_ticket_id:
        db.add(
            SyncOutbox(
                kind="reply",
                target_source_code=ticket.source_code,
                ticket_id=ticket.id,
                source_ticket_id=ticket.source_ticket_id,
                hub_issue_id=hub.id,
                payload={
                    "reply_content": hub.reply_content,
                    "reply_version": hub.reply_content_version,
                    "authored_by": hub.reply_authored_by or "agent:ai_cs",
                    "hub_short_code": hub.short_code,
                },
            )
        )
    logger.info(
        "reply_backfilled_to_merged_ticket",
        hub_issue_id=hub.id,
        ticket_id=ticket.id,
        version=hub.reply_content_version,
    )
    return True
