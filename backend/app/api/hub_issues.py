"""GET /api/hub-issues — list / detail.

  GET /api/hub-issues?type=&status=&assigned_user_id=&product=&module=&page=&page_size=
  GET /api/hub-issues/{hub_issue_id}             includes linked tickets (summary)

All authenticated users can read.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.repositories.ticket import HubIssueRepository, TicketRepository

router = APIRouter()


class HubIssueSummary(BaseModel):
    id: int
    short_code: str
    type: str
    status: str
    title: str
    priority: str | None
    occurrence_count: int
    product_line_code: str | None
    product: str | None
    module: str | None
    assigned_user_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    expected_resolved_at: datetime | None
    actual_resolved_at: datetime | None
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class LinkedTicket(BaseModel):
    id: int
    short_code: str
    source_code: str | None
    source_ticket_id: str | None
    status: str

    model_config = {"from_attributes": True}


class HubIssueDetail(HubIssueSummary):
    canonical_body: str | None
    # Operation-only
    reply_content: str | None
    reply_content_version: int
    reply_authored_by: str | None
    reply_updated_at: datetime | None
    # Bug_fix / Demand
    linear_uuid: str | None
    linear_identifier: str | None
    linear_status: str | None
    scheduled_iteration: str | None
    expected_released_at: datetime | None
    actual_released_at: datetime | None
    customer_verified_at: datetime | None
    # Internal_task
    feishu_task_id: str | None
    feishu_task_status: str | None
    feishu_task_synced_at: datetime | None
    # Type-immutable supersede chain
    superseded_by_hub_issue_id: int | None
    supersede_reason: str | None
    # linked tickets — convenience field for the detail page
    linked_tickets: list[LinkedTicket] = []


class HubIssueListResponse(BaseModel):
    items: list[HubIssueSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


@router.get("", response_model=HubIssueListResponse)
def list_hub_issues(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    type: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    assigned_user_id: int | None = Query(None),
    product: str | None = Query(None),
    module: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> HubIssueListResponse:
    p = HubIssueRepository(db).list_paginated(
        type_=type,
        status=status,
        assigned_user_id=assigned_user_id,
        product=product,
        module=module,
        page=page,
        page_size=page_size,
    )
    return HubIssueListResponse(
        items=[HubIssueSummary.model_validate(h) for h in p.items],
        total=p.total,
        page=p.page,
        page_size=p.page_size,
        has_more=p.has_more,
    )


@router.get("/{hub_issue_id}", response_model=HubIssueDetail)
def get_hub_issue(
    hub_issue_id: int,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> HubIssueDetail:
    hub = HubIssueRepository(db).get(hub_issue_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="hub_issue not found")
    linked = TicketRepository(db).list_for_hub_issue(hub_issue_id)
    detail = HubIssueDetail.model_validate(hub)
    detail.linked_tickets = [LinkedTicket.model_validate(t) for t in linked]
    return detail
