"""ticket / hub_issue queries used by SLAWatcher and ingest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket


class TicketRepository:
    """Read + write helpers. Soft-delete-aware on reads."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- ingest helpers ------------------------------------------------

    def find_by_source(self, source_code: str, source_ticket_id: str) -> Ticket | None:
        """Idempotency lookup: a webhook may fire multiple times for the same bill."""
        stmt = select(Ticket).where(
            Ticket.source_code == source_code,
            Ticket.source_ticket_id == source_ticket_id,
            Ticket.deleted_at.is_(None),
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def add(self, ticket: Ticket) -> Ticket:
        self._db.add(ticket)
        self._db.flush()
        return ticket

    def next_short_code(self, prefix: str = "TKT") -> str:
        """Generate the next short_code by counting current rows + 1.

        D1 fast path: simple counter; D2+ may switch to a sequence/redis counter
        if write contention becomes an issue.
        """
        from sqlalchemy import func

        n: int | None = self._db.execute(select(func.count(Ticket.id))).scalar()
        return f"{prefix}-{(n or 0) + 1:06d}"

    # ---- SLA scan ------------------------------------------------------

    def find_unreplied_overdue(
        self, *, threshold: timedelta, now: datetime | None = None
    ) -> list[Ticket]:
        """Tickets received before (now - threshold) without customer reply.

        Status whitelist: only the active ones (not done/superseded/rejected).
        """
        cutoff = (now or datetime.now(UTC)) - threshold
        active_statuses = (
            "received",
            "linked",
            "waiting_reply",
            "waiting_schedule",
            "scheduled",
            "in_progress",
            "code_merged",
            "released",
            "waiting_assign",
            "assigned",
        )
        stmt = (
            select(Ticket)
            .where(
                Ticket.deleted_at.is_(None),
                Ticket.received_at < cutoff,
                Ticket.customer_replied_at.is_(None),
                Ticket.status.in_(active_statuses),
            )
            .order_by(Ticket.received_at)
        )
        return list(self._db.execute(stmt).scalars().all())


class HubIssueRepository:
    """Read helpers for SLA scanning."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def find_overdue_by_type(
        self,
        *,
        type_thresholds: dict[str, timedelta],
        now: datetime | None = None,
    ) -> list[HubIssue]:
        """Per-type SLA scan.

        `type_thresholds`: {'Operation': 4h, 'Bug_fix': 8h, ...}; tickets older
        than their type's threshold AND still in an open status are returned.
        """
        if not type_thresholds:
            return []
        ts_now = now or datetime.now(UTC)
        active_open = (
            "created",
            "waiting_reply",
            "waiting_schedule",
            "in_progress",
            "scheduled",
            "waiting_assign",
            "assigned",
        )
        clauses = []
        for type_name, threshold in type_thresholds.items():
            cutoff = ts_now - threshold
            clauses.append((HubIssue.type == type_name) & (HubIssue.first_seen_at < cutoff))
        if not clauses:
            return []
        stmt = (
            select(HubIssue)
            .where(
                HubIssue.deleted_at.is_(None),
                HubIssue.actual_resolved_at.is_(None),
                HubIssue.status.in_(active_open),
                or_(*clauses),
            )
            .order_by(HubIssue.first_seen_at)
        )
        return list(self._db.execute(stmt).scalars().all())
