"""ticket / hub_issue queries used by SLAWatcher, ingest, and read API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Generic, TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket

T = TypeVar("T")


@dataclass(slots=True)
class Page(Generic[T]):
    items: list[T] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


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
        n: int | None = self._db.execute(select(func.count(Ticket.id))).scalar()
        return f"{prefix}-{(n or 0) + 1:06d}"

    # ---- read API ------------------------------------------------------

    def get(self, ticket_id: int) -> Ticket | None:
        """Get a non-deleted ticket by id."""
        t = self._db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            return None
        return t

    def list_by_ids(self, ticket_ids: list[int]) -> list[Ticket]:
        """Fetch multiple tickets by id in one query. Soft-delete-aware."""
        if not ticket_ids:
            return []
        stmt = select(Ticket).where(
            Ticket.id.in_(ticket_ids),
            Ticket.deleted_at.is_(None),
        )
        return list(self._db.execute(stmt).scalars().all())

    def list_paginated(
        self,
        *,
        source_code: str | None = None,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        unassigned_only: bool = False,
        customer_identity_id: int | None = None,
        hub_issue_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[Ticket]:
        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)

        base = select(Ticket).where(Ticket.deleted_at.is_(None))
        count_base = select(func.count(Ticket.id)).where(Ticket.deleted_at.is_(None))
        if source_code:
            base = base.where(Ticket.source_code == source_code)
            count_base = count_base.where(Ticket.source_code == source_code)
        if type_:
            base = base.where(Ticket.type == type_)
            count_base = count_base.where(Ticket.type == type_)
        if status:
            base = base.where(Ticket.status == status)
            count_base = count_base.where(Ticket.status == status)
        if assigned_user_id is not None:
            base = base.where(Ticket.assigned_user_id == assigned_user_id)
            count_base = count_base.where(Ticket.assigned_user_id == assigned_user_id)
        if unassigned_only:
            base = base.where(Ticket.assigned_user_id.is_(None))
            count_base = count_base.where(Ticket.assigned_user_id.is_(None))
        if customer_identity_id is not None:
            base = base.where(Ticket.customer_identity_id == customer_identity_id)
            count_base = count_base.where(Ticket.customer_identity_id == customer_identity_id)
        if hub_issue_id is not None:
            base = base.where(Ticket.hub_issue_id == hub_issue_id)
            count_base = count_base.where(Ticket.hub_issue_id == hub_issue_id)

        total = self._db.execute(count_base).scalar() or 0
        rows_stmt = (
            base.order_by(Ticket.received_at.desc(), Ticket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self._db.execute(rows_stmt).scalars().all())
        return Page(items=items, total=total, page=page, page_size=page_size)

    def list_for_hub_issue(self, hub_issue_id: int) -> list[Ticket]:
        """All non-deleted tickets currently linked to a hub_issue."""
        stmt = (
            select(Ticket)
            .where(
                Ticket.hub_issue_id == hub_issue_id,
                Ticket.deleted_at.is_(None),
            )
            .order_by(Ticket.received_at.desc())
        )
        return list(self._db.execute(stmt).scalars().all())

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
    """Read helpers for SLA scanning + read API."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- read API ------------------------------------------------------

    def get(self, hub_issue_id: int) -> HubIssue | None:
        h = self._db.get(HubIssue, hub_issue_id)
        if h is None or h.deleted_at is not None:
            return None
        return h

    def list_paginated(
        self,
        *,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        product: str | None = None,
        module: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[HubIssue]:
        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)

        base = select(HubIssue).where(HubIssue.deleted_at.is_(None))
        count_base = select(func.count(HubIssue.id)).where(HubIssue.deleted_at.is_(None))
        if type_:
            base = base.where(HubIssue.type == type_)
            count_base = count_base.where(HubIssue.type == type_)
        if status:
            base = base.where(HubIssue.status == status)
            count_base = count_base.where(HubIssue.status == status)
        if assigned_user_id is not None:
            base = base.where(HubIssue.assigned_user_id == assigned_user_id)
            count_base = count_base.where(HubIssue.assigned_user_id == assigned_user_id)
        if product:
            base = base.where(HubIssue.product == product)
            count_base = count_base.where(HubIssue.product == product)
        if module:
            base = base.where(HubIssue.module == module)
            count_base = count_base.where(HubIssue.module == module)

        total = self._db.execute(count_base).scalar() or 0
        rows_stmt = (
            base.order_by(HubIssue.last_seen_at.desc(), HubIssue.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self._db.execute(rows_stmt).scalars().all())
        return Page(items=items, total=total, page=page, page_size=page_size)

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
