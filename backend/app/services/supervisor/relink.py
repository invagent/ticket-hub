"""SupervisorRelinkService — re-link a ticket from one hub_issue to another.

Per upgrade_plan.md §6.4 / §11.2-D9:

  Supervisor "事后修正" entry point. Operations:
    1. Validate ticket + new hub_issue exist (and not soft-deleted).
    2. Validate the operator has role IN ('supervisor','admin').
    3. Close the current ticket_hub_issue_history row (effective_to = now()).
    4. Insert a new ticket_hub_issue_history row (effective_to = NULL = current).
    5. Update tickets.hub_issue_id to new value.
    6. (D3) Revert related agent_decisions (status='reverted', reverted_at=now()).

  Idempotent: relinking to the same hub_issue is a no-op.

  D1 scope: steps 1-5. Step 6 lands when agent_decisions table arrives in D3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue, Ticket, TicketHubIssueHistory, User

logger = get_logger(__name__)


class RelinkError(Exception):
    """Base class for relink validation failures."""


class TicketNotFoundError(RelinkError):
    pass


class HubIssueNotFoundError(RelinkError):
    pass


class PermissionDeniedError(RelinkError):
    pass


@dataclass(slots=True, frozen=True)
class RelinkRequest:
    ticket_id: int
    new_hub_issue_id: int
    supervisor_user_id: int
    reason: str = ""


@dataclass(slots=True, frozen=True)
class RelinkResult:
    ticket_id: int
    old_hub_issue_id: int | None
    new_hub_issue_id: int
    closed_history_id: int | None  # id of the row whose effective_to we just set
    new_history_id: int  # id of the new "current" row
    no_op: bool  # True when caller asked to relink to the already-current target


class SupervisorRelinkService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def relink(self, req: RelinkRequest, *, now: datetime | None = None) -> RelinkResult:
        ts_now = now or datetime.now(UTC)

        # 1. Validate operator role
        operator = self._db.get(User, req.supervisor_user_id)
        if operator is None or operator.role not in ("supervisor", "admin"):
            raise PermissionDeniedError(f"user {req.supervisor_user_id} is not a supervisor/admin")

        # 2. Validate ticket + hub_issue
        ticket = self._db.get(Ticket, req.ticket_id)
        if ticket is None or ticket.deleted_at is not None:
            raise TicketNotFoundError(f"ticket {req.ticket_id} not found")
        new_hub = self._db.get(HubIssue, req.new_hub_issue_id)
        if new_hub is None or new_hub.deleted_at is not None:
            raise HubIssueNotFoundError(f"hub_issue {req.new_hub_issue_id} not found")

        old_hub_issue_id = ticket.hub_issue_id

        # 3. Idempotency: already linked to target → no-op
        if old_hub_issue_id == req.new_hub_issue_id:
            current_history = self._find_current_history(req.ticket_id)
            return RelinkResult(
                ticket_id=req.ticket_id,
                old_hub_issue_id=old_hub_issue_id,
                new_hub_issue_id=req.new_hub_issue_id,
                closed_history_id=None,
                new_history_id=current_history.id if current_history else 0,
                no_op=True,
            )

        # 4. Close current history row (if any)
        closed_id: int | None = None
        current = self._find_current_history(req.ticket_id)
        if current is not None:
            current.effective_to = ts_now
            self._db.flush()
            closed_id = current.id

        # 5. Insert new history row
        new_row = TicketHubIssueHistory(
            ticket_id=req.ticket_id,
            hub_issue_id=req.new_hub_issue_id,
            effective_from=ts_now,
            effective_to=None,
            change_reason=req.reason,
            human_confirmed=True,  # supervisor relink == confirmed
        )
        self._db.add(new_row)
        self._db.flush()

        # 6. Update tickets.hub_issue_id
        self._db.execute(
            update(Ticket)
            .where(Ticket.id == req.ticket_id)
            .values(hub_issue_id=req.new_hub_issue_id)
        )

        logger.info(
            "supervisor_relink_committed",
            ticket_id=req.ticket_id,
            old_hub_issue_id=old_hub_issue_id,
            new_hub_issue_id=req.new_hub_issue_id,
            supervisor_user_id=req.supervisor_user_id,
            reason=req.reason,
        )
        return RelinkResult(
            ticket_id=req.ticket_id,
            old_hub_issue_id=old_hub_issue_id,
            new_hub_issue_id=req.new_hub_issue_id,
            closed_history_id=closed_id,
            new_history_id=new_row.id,
            no_op=False,
        )

    def _find_current_history(self, ticket_id: int) -> TicketHubIssueHistory | None:
        stmt = (
            select(TicketHubIssueHistory)
            .where(
                TicketHubIssueHistory.ticket_id == ticket_id,
                TicketHubIssueHistory.effective_to.is_(None),
            )
            .order_by(TicketHubIssueHistory.effective_from.desc())
            .limit(1)
        )
        return self._db.execute(stmt).scalar_one_or_none()
