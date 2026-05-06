"""ticket_hub_issue_history reads (write goes through SupervisorRelinkService)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TicketHubIssueHistory


class TicketHubIssueHistoryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_for_ticket(self, ticket_id: int, *, limit: int = 200) -> list[TicketHubIssueHistory]:
        """Relink history for a ticket, oldest first."""
        stmt = (
            select(TicketHubIssueHistory)
            .where(TicketHubIssueHistory.ticket_id == ticket_id)
            .order_by(
                TicketHubIssueHistory.effective_from.asc(),
                TicketHubIssueHistory.id.asc(),
            )
            .limit(min(max(limit, 1), 1000))
        )
        return list(self._db.execute(stmt).scalars().all())
