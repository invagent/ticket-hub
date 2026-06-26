"""GET /api/hub-issues — list / detail；POST reply（D4 第②段）.

  GET  /api/hub-issues?type=&status=&assigned_user_id=&product=&module=&page=&page_size=
  GET  /api/hub-issues/{hub_issue_id}            includes linked tickets (summary)
  POST /api/hub-issues/{hub_issue_id}/reply      author Operation reply (supervisor)

All authenticated users can read; replies require supervisor.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_supervisor, require_user
from app.core.logging import get_logger
from app.db import get_session
from app.repositories.ticket import HubIssueRepository, TicketRepository
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.cascade.supply_sync import SupplySyncError, request_supply

router = APIRouter()
logger = get_logger(__name__)


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
    # 分视图的类型专属列（D4 第②段）
    linear_identifier: str | None  # Bug_fix / Demand
    linear_status: str | None
    reply_content_version: int  # Operation: 0 = 未回复
    reply_updated_at: datetime | None
    feishu_task_status: str | None  # Internal_task

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
    # Operation-only（其余 Operation 字段已在 Summary）
    reply_content: str | None
    reply_authored_by: str | None
    # Bug_fix / Demand（identifier/status 已在 Summary）
    linear_uuid: str | None
    scheduled_iteration: str | None
    expected_released_at: datetime | None
    actual_released_at: datetime | None
    customer_verified_at: datetime | None
    # Internal_task（status 已在 Summary）
    feishu_task_id: str | None
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


# ---- Operation reply (决策 15, D4 第②段) ------------------------------------


class AuthorReplyBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class AuthorReplyResponse(BaseModel):
    hub_issue_id: int
    version: int
    cascaded_ticket_count: int
    outbox_count: int  # 入队待回写源系统的条数（D5 sender 消费）


@router.post("/{hub_issue_id}/reply", response_model=AuthorReplyResponse)
def author_reply_endpoint(
    hub_issue_id: int,
    body: AuthorReplyBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> AuthorReplyResponse:
    """Author/replace the Operation reply. Cascades to linked tickets'
    cached_reply and enqueues sync_outbox rows for source write-back."""
    try:
        result = author_reply(
            db, hub_issue_id, content=body.content, authored_by=f"user:{user.name}"
        )
    except ReplySyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "hub_issue_reply_authored",
        hub_issue_id=hub_issue_id,
        version=result.version,
        operator_user_id=user.user_id,
    )
    return AuthorReplyResponse(
        hub_issue_id=result.hub_issue_id,
        version=result.version,
        cascaded_ticket_count=len(result.cascaded_ticket_ids),
        outbox_count=len(result.outbox_ids),
    )


# ---- Supply request (补料, D4 第②段) ----------------------------------------


class RequestSupplyBody(BaseModel):
    note: str = Field(..., min_length=1, max_length=4000)


class RequestSupplyResponse(BaseModel):
    hub_issue_id: int
    ticket_count: int
    outbox_count: int  # 入队待回写 KSM supplyKsmOrder 的条数


@router.post("/{hub_issue_id}/request-supply", response_model=RequestSupplyResponse)
def request_supply_endpoint(
    hub_issue_id: int,
    body: RequestSupplyBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RequestSupplyResponse:
    """Ask the customer for more info (补料). Enqueues a supply sync_outbox row
    per linked sourced ticket; the KSM sender drains them into supplyKsmOrder."""
    try:
        result = request_supply(db, hub_issue_id, note=body.note, requested_by=f"user:{user.name}")
    except SupplySyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "hub_issue_supply_requested",
        hub_issue_id=hub_issue_id,
        tickets=len(result.ticket_ids),
        operator_user_id=user.user_id,
    )
    return RequestSupplyResponse(
        hub_issue_id=result.hub_issue_id,
        ticket_count=len(result.ticket_ids),
        outbox_count=len(result.outbox_ids),
    )
