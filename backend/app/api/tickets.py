"""GET /api/tickets — list / detail.

  GET /api/tickets?source_code=&type=&status=&assigned_user_id=&page=&page_size=
  GET /api/tickets/{ticket_id}

All authenticated users can read (any role). D2 may add row-level visibility
(only own + supervisor sees subordinates) — for D1 keep open within the org.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.repositories.ticket import TicketRepository

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
    return TicketListResponse(
        items=[TicketSummary.model_validate(t) for t in p.items],
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
    return TicketDetail.model_validate(ticket)
