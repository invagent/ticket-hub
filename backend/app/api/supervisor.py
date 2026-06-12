"""Supervisor work-bench API endpoints.

  GET  /api/supervisor/inbox                       — pending notifications
  POST /api/supervisor/notifications/{id}/ack      — mark acknowledged
  POST /api/supervisor/relink                      — re-link ticket↔hub_issue
  GET  /api/supervisor/config-warnings             — system configuration gaps
  POST /api/supervisor/reroute                     — re-trigger routing for unassigned tickets
  GET  /api/supervisor/split-proposals             — pending split_ticket proposals
  POST /api/supervisor/execute-split               — materialize a split_ticket proposal
  POST /api/supervisor/dismiss-split               — decline an unmaterialized proposal
  POST /api/supervisor/revert-split                — undo a materialized split
  POST /api/supervisor/create-hub-issue            — graduate a ticket to a hub_issue

All endpoints require role IN ('supervisor', 'admin').
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_supervisor
from app.core.logging import get_logger
from app.db import get_session
from app.repositories.notification_log import NotificationLogRepository
from app.services.agents.classify import classify_ticket
from app.services.agents.split import (
    SplitError,
    dismiss_split_proposal,
    execute_split,
    list_pending_split_proposals,
    revert_split,
)
from app.services.hub_issues.creator import (
    HubIssueCreateError,
    ensure_hub_issue_for_ticket,
)
from app.services.hub_issues.linear_push import push_hub_issue_to_linear
from app.services.supervisor.config_warnings import get_config_warnings
from app.services.supervisor.relink import (
    HubIssueNotFoundError,
    PermissionDeniedError,
    RelinkRequest,
    SupervisorRelinkService,
    TicketNotFoundError,
)
from app.services.supervisor.reroute import RerouteRequest, RerouteService

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs -----------------------------------------------------------------


class InboxItem(BaseModel):
    id: int
    notify_type: str
    channel: str
    related_entity_type: str | None
    related_entity_id: int | None
    payload: dict[str, Any]
    sent_at: datetime


class InboxResponse(BaseModel):
    items: list[InboxItem]


class AckResponse(BaseModel):
    notification_id: int
    acknowledged_at: datetime


class RelinkBody(BaseModel):
    ticket_id: int
    new_hub_issue_id: int
    reason: str = ""


class RelinkResponse(BaseModel):
    ticket_id: int
    old_hub_issue_id: int | None
    new_hub_issue_id: int
    no_op: bool
    closed_history_id: int | None
    new_history_id: int


class ConfigWarningItem(BaseModel):
    code: str
    product_line_code: str | None
    module: str | None
    detail: str


class ConfigWarningsResponse(BaseModel):
    warnings: list[ConfigWarningItem]


class RerouteBody(BaseModel):
    ticket_ids: list[int] = Field(..., min_length=1, max_length=50)


class RerouteItemOut(BaseModel):
    ticket_id: int
    short_code: str
    success: bool
    decision: str
    assigned_user_ids: list[int]
    message: str


class RerouteResponse(BaseModel):
    results: list[RerouteItemOut]
    assigned_count: int
    no_match_count: int


# ---- endpoints ------------------------------------------------------------


@router.get("/inbox", response_model=InboxResponse)
def list_inbox(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 100,
) -> InboxResponse:
    rows = NotificationLogRepository(db).list_pending_for_recipient(
        user.user_id, limit=min(limit, 200)
    )
    return InboxResponse(
        items=[
            InboxItem(
                id=r.id,
                notify_type=r.notify_type,
                channel=r.channel,
                related_entity_type=r.related_entity_type,
                related_entity_id=r.related_entity_id,
                payload=r.payload,
                sent_at=r.sent_at,
            )
            for r in rows
        ]
    )


@router.post("/notifications/{notification_id}/ack", response_model=AckResponse)
def ack_notification(
    notification_id: int,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> AckResponse:
    repo = NotificationLogRepository(db)
    row = repo.get(notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if row.recipient_user_id != user.user_id:
        # Non-recipients cannot ack each other's notifications (audit cleanliness).
        # Admin override could be added later if needed.
        raise HTTPException(
            status_code=403,
            detail="cannot ack a notification addressed to another user",
        )
    if row.acknowledged_at is not None:
        return AckResponse(notification_id=row.id, acknowledged_at=row.acknowledged_at)
    repo.acknowledge(notification_id)
    db.commit()
    db.refresh(row)
    logger.info(
        "supervisor_ack",
        notification_id=notification_id,
        supervisor_user_id=user.user_id,
    )
    assert row.acknowledged_at is not None  # just set
    return AckResponse(notification_id=row.id, acknowledged_at=row.acknowledged_at)


@router.post("/relink", response_model=RelinkResponse)
def relink_ticket(
    body: RelinkBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RelinkResponse:
    svc = SupervisorRelinkService(db)
    try:
        result = svc.relink(
            RelinkRequest(
                ticket_id=body.ticket_id,
                new_hub_issue_id=body.new_hub_issue_id,
                supervisor_user_id=user.user_id,
                reason=body.reason,
            )
        )
    except (TicketNotFoundError, HubIssueNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionDeniedError as e:
        # Only happens if JWT role got out of sync with DB; treat as 403
        raise HTTPException(status_code=403, detail=str(e)) from e
    db.commit()
    return RelinkResponse(
        ticket_id=result.ticket_id,
        old_hub_issue_id=result.old_hub_issue_id,
        new_hub_issue_id=result.new_hub_issue_id,
        no_op=result.no_op,
        closed_history_id=result.closed_history_id,
        new_history_id=result.new_history_id,
    )


@router.get("/config-warnings", response_model=ConfigWarningsResponse)
def list_config_warnings(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> ConfigWarningsResponse:
    items = get_config_warnings(db)
    return ConfigWarningsResponse(
        warnings=[
            ConfigWarningItem(
                code=w.code,
                product_line_code=w.product_line_code,
                module=w.module,
                detail=w.detail,
            )
            for w in items
        ]
    )


@router.post("/reroute", response_model=RerouteResponse)
def reroute_tickets(
    body: RerouteBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RerouteResponse:
    result = RerouteService(db).reroute(
        RerouteRequest(
            ticket_ids=body.ticket_ids,
            operator_user_id=user.user_id,
        )
    )
    db.commit()
    logger.info(
        "supervisor_reroute",
        ticket_ids=body.ticket_ids,
        assigned_count=result.assigned_count,
        no_match_count=result.no_match_count,
        operator_user_id=user.user_id,
    )
    return RerouteResponse(
        results=[
            RerouteItemOut(
                ticket_id=r.ticket_id,
                short_code=r.short_code,
                success=r.success,
                decision=r.decision,
                assigned_user_ids=r.assigned_user_ids,
                message=r.message,
            )
            for r in result.results
        ],
        assigned_count=result.assigned_count,
        no_match_count=result.no_match_count,
    )


# ---- split execute / revert (D3-D) -----------------------------------------


class SplitSubIssueOut(BaseModel):
    title: str
    summary: str


class SplitProposalItem(BaseModel):
    decision_id: int
    ticket_id: int
    ticket_short_code: str
    ticket_title: str | None
    confidence: float
    reason: str
    sub_issues: list[SplitSubIssueOut]
    created_at: datetime


class SplitProposalsResponse(BaseModel):
    items: list[SplitProposalItem]


class ExecuteSplitBody(BaseModel):
    decision_id: int


class DismissSplitBody(BaseModel):
    decision_id: int
    reason: str | None = Field(default=None, max_length=500)


class DismissSplitResponse(BaseModel):
    decision_id: int


class ExecuteSplitResponse(BaseModel):
    decision_id: int
    parent_ticket_id: int
    child_ticket_ids: list[int]


class RevertSplitBody(BaseModel):
    decision_id: int
    reason: str | None = Field(default=None, max_length=500)


class RevertSplitResponse(BaseModel):
    decision_id: int
    parent_ticket_id: int
    deleted_child_ids: list[int]


@router.get("/split-proposals", response_model=SplitProposalsResponse)
def list_split_proposals(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> SplitProposalsResponse:
    """Pending split_ticket proposals awaiting supervisor action (execute or
    dismiss). Materialized and reverted proposals are excluded."""
    rows = list_pending_split_proposals(db, limit=min(limit, 100))
    return SplitProposalsResponse(
        items=[
            SplitProposalItem(
                decision_id=d.id,
                ticket_id=t.id,
                ticket_short_code=t.short_code,
                ticket_title=t.title,
                confidence=float(d.proposal.get("confidence") or 0.0),
                reason=str(d.proposal.get("reason") or ""),
                sub_issues=[
                    SplitSubIssueOut(
                        title=str(s.get("title") or ""),
                        summary=str(s.get("summary") or ""),
                    )
                    for s in (d.proposal.get("sub_issues") or [])
                ],
                created_at=d.created_at,
            )
            for d, t in rows
        ]
    )


@router.post("/dismiss-split", response_model=DismissSplitResponse)
def dismiss_split_endpoint(
    body: DismissSplitBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DismissSplitResponse:
    """Decline an unmaterialized split proposal (audit-preserving)."""
    try:
        decision_id = dismiss_split_proposal(
            body.decision_id,
            dismissed_by=f"user:{user.name}",
            reason=body.reason,
            db=db,
        )
    except SplitError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "supervisor_dismiss_split",
        decision_id=decision_id,
        operator_user_id=user.user_id,
    )
    return DismissSplitResponse(decision_id=decision_id)


@router.post("/execute-split", response_model=ExecuteSplitResponse)
def execute_split_endpoint(
    body: ExecuteSplitBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> ExecuteSplitResponse:
    """Materialize a pending split_ticket proposal into Child tickets.

    Children are classified asynchronously after the response (LLM call —
    must not block the supervisor's request).
    """
    try:
        result = execute_split(body.decision_id, executed_by=f"user:{user.name}", db=db)
    except SplitError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    for child_id in result.child_ticket_ids:
        background_tasks.add_task(classify_ticket, child_id)
    logger.info(
        "supervisor_execute_split",
        decision_id=body.decision_id,
        parent_ticket_id=result.parent_ticket_id,
        child_ticket_ids=result.child_ticket_ids,
        operator_user_id=user.user_id,
    )
    return ExecuteSplitResponse(
        decision_id=result.decision_id,
        parent_ticket_id=result.parent_ticket_id,
        child_ticket_ids=result.child_ticket_ids,
    )


class CreateHubIssueBody(BaseModel):
    ticket_id: int
    # Optional supervisor override; defaults to the ticket's predicted_type.
    type: str | None = Field(default=None, pattern="^(Operation|Bug_fix|Demand|Internal_task)$")


class CreateHubIssueResponse(BaseModel):
    hub_issue_id: int
    hub_issue_short_code: str
    ticket_id: int
    type: str
    created: bool


@router.post("/create-hub-issue", response_model=CreateHubIssueResponse)
def create_hub_issue_endpoint(
    body: CreateHubIssueBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> CreateHubIssueResponse:
    """Graduate a ticket to a hub_issue (manual path, no confidence gate).

    Bug_fix/Demand issues are pushed to Linear asynchronously when
    LINEAR_PUSH_ENABLED is on (the push itself re-checks all gates).
    """
    try:
        result = ensure_hub_issue_for_ticket(
            body.ticket_id,
            created_by=f"user:{user.name}",
            type_override=body.type,
            db=db,
        )
    except HubIssueCreateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if result.created and result.type in ("Bug_fix", "Demand"):
        background_tasks.add_task(push_hub_issue_to_linear, result.hub_issue_id)
    logger.info(
        "supervisor_create_hub_issue",
        ticket_id=body.ticket_id,
        hub_issue_id=result.hub_issue_id,
        type=result.type,
        created=result.created,
        operator_user_id=user.user_id,
    )
    return CreateHubIssueResponse(
        hub_issue_id=result.hub_issue_id,
        hub_issue_short_code=result.hub_issue_short_code,
        ticket_id=result.ticket_id,
        type=result.type,
        created=result.created,
    )


@router.post("/revert-split", response_model=RevertSplitResponse)
def revert_split_endpoint(
    body: RevertSplitBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RevertSplitResponse:
    """Undo a materialized split: soft-delete children (refused if any child
    is already in progress), restore the parent to Raw."""
    try:
        result = revert_split(
            body.decision_id,
            reverted_by=f"user:{user.name}",
            reason=body.reason,
            db=db,
        )
    except SplitError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "supervisor_revert_split",
        decision_id=body.decision_id,
        parent_ticket_id=result.parent_ticket_id,
        deleted_child_ids=result.deleted_child_ids,
        operator_user_id=user.user_id,
    )
    return RevertSplitResponse(
        decision_id=result.decision_id,
        parent_ticket_id=result.parent_ticket_id,
        deleted_child_ids=result.deleted_child_ids,
    )
