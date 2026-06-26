"""Supply request cascade (D4 第②段) — ask the customer for more info.

A supervisor requests "补料" on a hub_issue; this enqueues one sync_outbox
row (kind='supply') per linked SOURCED ticket. The KSM sender drains those
rows into supplyKsmOrder ("补充资料"). Mirrors reply_sync's fan-out, but a
supply request is an action (not versioned content) so it carries no hub
state change — just the outbox rows + a status_history audit line.

Child tickets (split products, source_code NULL) have no source system to
ask, so they are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)


class SupplySyncError(Exception):
    """Supply can't be requested; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class SupplyResult:
    hub_issue_id: int
    ticket_ids: list[int]
    outbox_ids: list[int]


def request_supply(
    db: Session,
    hub_issue_id: int,
    *,
    note: str,
    requested_by: str,
) -> SupplyResult:
    """Enqueue a supply (补料) writeback for every sourced ticket. Commits."""
    note = (note or "").strip()
    if not note:
        raise SupplySyncError("supply note is empty")

    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise SupplySyncError(f"hub_issue {hub_issue_id} not found")

    tickets = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    history = StatusHistoryRepository(db)
    ticket_ids: list[int] = []
    outbox_ids: list[int] = []
    for t in tickets:
        if not (t.source_code and t.source_ticket_id):
            continue
        ticket_ids.append(t.id)
        row = SyncOutbox(
            kind="supply",
            target_source_code=t.source_code,
            ticket_id=t.id,
            source_ticket_id=t.source_ticket_id,
            hub_issue_id=hub.id,
            payload={
                "supply_note": note,
                "hub_short_code": hub.short_code,
                "requested_by": requested_by,
            },
        )
        db.add(row)
        db.flush()
        outbox_ids.append(row.id)
        history.record(
            entity_type="ticket",
            entity_id=t.id,
            from_status=t.status,
            to_status=t.status,
            changed_by=requested_by,
            reason=f"补料请求 from {hub.short_code}: {note[:120]}",
        )

    db.commit()
    logger.info(
        "supply_requested",
        hub_issue_id=hub.id,
        tickets=len(ticket_ids),
        outbox=len(outbox_ids),
        requested_by=requested_by,
    )
    return SupplyResult(hub_issue_id=hub.id, ticket_ids=ticket_ids, outbox_ids=outbox_ids)
