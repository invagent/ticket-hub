"""Status cascade (决策 14, D4 第②段) — hub_issue status fans out to tickets.

Single entrypoint `apply_hub_status` used by every hub-status writer (today:
linear_status_sync; later: supervisor manual transitions, KSM 反向 ack)
so the cascade semantics live in exactly one place:

    hub_issue.status = to_status        + status_history
    linked ACTIVE tickets → to_status   + status_history     (only for the
                                          statuses both sides understand)
    sync_outbox (kind='status')         one row per SOURCED ticket — D5
                                          sender 回写源系统

Conservative by design:
    * only 'in_progress' / 'released' cascade to tickets — other hub statuses
      (pending, created, …) are hub-internal workflow
    * tickets already in a terminal state (done/closed/rejected/superseded)
      are never touched
    * released also stamps ticket.actual_released_at when empty
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

# hub statuses that fan out to tickets (identical value on both sides)
_TICKET_CASCADE_STATUSES = frozenset({"in_progress", "released"})

_TICKET_TERMINAL_STATUSES = frozenset({"done", "closed", "rejected", "superseded"})


@dataclass(slots=True)
class CascadeResult:
    hub_issue_id: int
    from_status: str
    to_status: str
    changed: bool = False
    cascaded_ticket_ids: list[int] = field(default_factory=list)
    outbox_ids: list[int] = field(default_factory=list)


def apply_hub_status(
    db: Session,
    hub: HubIssue,
    *,
    to_status: str,
    changed_by: str,
    reason: str | None = None,
) -> CascadeResult:
    """Transition the hub_issue and cascade. Flushes; caller commits
    (writers batch several hubs per transaction, e.g. the Linear poller)."""
    result = CascadeResult(hub_issue_id=hub.id, from_status=hub.status, to_status=to_status)
    if hub.status == to_status:
        return result

    history = StatusHistoryRepository(db)
    now = datetime.now(UTC)

    prev = hub.status
    hub.status = to_status
    if to_status == "released" and hub.actual_released_at is None:
        hub.actual_released_at = now
    history.record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status=to_status,
        changed_by=changed_by,
        reason=reason,
    )
    result.changed = True

    if to_status not in _TICKET_CASCADE_STATUSES:
        return result

    tickets = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    for t in tickets:
        if t.status == to_status or t.status in _TICKET_TERMINAL_STATUSES:
            continue
        t_prev = t.status
        t.status = to_status
        if to_status == "released" and t.actual_released_at is None:
            t.actual_released_at = now
        history.record(
            entity_type="ticket",
            entity_id=t.id,
            from_status=t_prev,
            to_status=to_status,
            changed_by=changed_by,
            reason=f"cascade from {hub.short_code}" + (f": {reason}" if reason else ""),
        )
        result.cascaded_ticket_ids.append(t.id)
        if t.source_code and t.source_ticket_id:
            row = SyncOutbox(
                kind="status",
                target_source_code=t.source_code,
                ticket_id=t.id,
                source_ticket_id=t.source_ticket_id,
                hub_issue_id=hub.id,
                payload={
                    "to_status": to_status,
                    "hub_short_code": hub.short_code,
                    "reason": reason,
                },
            )
            db.add(row)
            db.flush()
            result.outbox_ids.append(row.id)

    logger.info(
        "status_cascaded",
        hub_issue_id=hub.id,
        from_status=prev,
        to_status=to_status,
        cascaded=len(result.cascaded_ticket_ids),
        outbox=len(result.outbox_ids),
        changed_by=changed_by,
    )
    return result
