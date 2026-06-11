"""GET /api/tickets — list / detail / history.

  GET /api/tickets?source_code=&type=&status=&assigned_user_id=&page=&page_size=
  GET /api/tickets/{ticket_id}
  GET /api/tickets/{ticket_id}/history          status + relink merged timeline

All authenticated users can read (any role). D2 may add row-level visibility
(only own + supervisor sees subordinates) — for D1 keep open within the org.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.models import Customer, CustomerIdentity, User
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.repositories.ticket_hub_issue_history import TicketHubIssueHistoryRepository

router = APIRouter()


class TicketSummary(BaseModel):
    id: int
    short_code: str
    source_code: str | None
    source_ticket_id: str | None
    type: str
    status: str
    title: str | None
    customer_identity_id: int | None
    product_line_code: str | None
    module: str | None
    feature: str | None
    assigned_user_id: int | None
    assigned_user_name: str | None = None
    predicted_type: str | None = None
    hub_issue_id: int | None
    received_at: datetime | None
    customer_replied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketDetail(TicketSummary):
    body: str | None
    body_html: str | None
    reporter: dict[str, Any] | None
    source_payload: dict[str, Any] | None
    source_status: str | None
    parent_ticket_id: int | None
    children_ticket_ids: list[int] | None
    expected_resolved_at: datetime | None
    actual_resolved_at: datetime | None
    actual_replied_at: datetime | None
    cached_reply_content: str | None
    cached_reply_version: int | None
    # enriched display fields (not on ORM, set manually in get_ticket)
    assigned_user_name: str | None = None
    customer_display_name: str | None = None
    customer_id: int | None = None
    reporter_name: str | None = None


class TicketListResponse(BaseModel):
    items: list[TicketSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


@router.get("", response_model=TicketListResponse)
def list_tickets(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    source_code: str | None = Query(None),
    type: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    assigned_user_id: int | None = Query(None),
    customer_identity_id: int | None = Query(None),
    hub_issue_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> TicketListResponse:
    p = TicketRepository(db).list_paginated(
        source_code=source_code,
        type_=type,
        status=status,
        assigned_user_id=assigned_user_id,
        customer_identity_id=customer_identity_id,
        hub_issue_id=hub_issue_id,
        page=page,
        page_size=page_size,
    )
    # batch-load user names to avoid N+1
    user_ids = {t.assigned_user_id for t in p.items if t.assigned_user_id is not None}
    user_name_map: dict[int, str] = {}
    if user_ids:
        rows = db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
        user_name_map = {r.id: r.name for r in rows}

    def _to_summary(t: Any) -> TicketSummary:
        s = TicketSummary.model_validate(t)
        if t.assigned_user_id is not None:
            s.assigned_user_name = user_name_map.get(t.assigned_user_id)
        return s

    return TicketListResponse(
        items=[_to_summary(t) for t in p.items],
        total=p.total,
        page=p.page,
        page_size=p.page_size,
        has_more=p.has_more,
    )


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(
    ticket_id: int,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> TicketDetail:
    ticket = TicketRepository(db).get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    detail = TicketDetail.model_validate(ticket)
    if ticket.assigned_user_id is not None:
        user = db.get(User, ticket.assigned_user_id)
        detail.assigned_user_name = user.name if user else None
    if ticket.customer_identity_id is not None:
        identity = db.get(CustomerIdentity, ticket.customer_identity_id)
        if identity is not None:
            detail.customer_id = identity.customer_id
            customer = db.get(Customer, identity.customer_id)
            if customer is not None:
                detail.customer_display_name = customer.display_name or identity.raw_name
            else:
                detail.customer_display_name = identity.raw_name
    if ticket.reporter and isinstance(ticket.reporter, dict):
        detail.reporter_name = ticket.reporter.get("feedback_user") or ticket.reporter.get(
            "linkman"
        )
    return detail


# ---- /history -------------------------------------------------------------


class HistoryEvent(BaseModel):
    """One row in the merged ticket timeline.

    Two `kind` values are emitted:
      - 'status'        — a status_history transition (from→to)
      - 'hub_issue_link' — a ticket_hub_issue_history row (effective_from start
                           of an association; effective_to non-null = closed)

    Sorted by `occurred_at` ascending in the response (oldest → newest); the
    frontend reverses for display.
    """

    kind: Literal["status", "hub_issue_link"]
    occurred_at: datetime
    # status fields (None when kind != 'status')
    from_status: str | None = None
    to_status: str | None = None
    changed_by: str | None = None
    reason: str | None = None
    metadata_: dict[str, Any] | None = None
    # hub_issue_link fields (None when kind != 'hub_issue_link')
    hub_issue_id: int | None = None
    effective_to: datetime | None = None
    change_reason: str | None = None
    human_confirmed: bool | None = None


class HistoryResponse(BaseModel):
    ticket_id: int
    items: list[HistoryEvent]


@router.get("/{ticket_id}/history", response_model=HistoryResponse)
def get_ticket_history(
    ticket_id: int,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> HistoryResponse:
    if TicketRepository(db).get(ticket_id) is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    status_rows = StatusHistoryRepository(db).find_for_entity(
        entity_type="ticket", entity_id=ticket_id
    )
    relink_rows = TicketHubIssueHistoryRepository(db).find_for_ticket(ticket_id)

    events: list[HistoryEvent] = []
    for s in status_rows:
        events.append(
            HistoryEvent(
                kind="status",
                occurred_at=s.changed_at,
                from_status=s.from_status,
                to_status=s.to_status,
                changed_by=s.changed_by,
                reason=s.reason,
                metadata_=s.metadata_,
            )
        )
    for h in relink_rows:
        events.append(
            HistoryEvent(
                kind="hub_issue_link",
                occurred_at=h.effective_from,
                hub_issue_id=h.hub_issue_id,
                effective_to=h.effective_to,
                change_reason=h.change_reason,
                human_confirmed=h.human_confirmed,
            )
        )
    # Stable merge sort: status and relink with the same timestamp keep
    # status-first (status is the cause; relink is often the effect).
    events.sort(key=lambda e: (e.occurred_at, 0 if e.kind == "status" else 1))
    return HistoryResponse(ticket_id=ticket_id, items=events)
