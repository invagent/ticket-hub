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
  GET  /api/supervisor/dedup-proposals             — pending dedup_link proposals
  POST /api/supervisor/execute-dedup               — merge duplicate onto original's hub_issue
  POST /api/supervisor/dismiss-dedup               — decline a dedup proposal
  GET  /api/supervisor/pending-hub-issues          — Linear push blocked, awaiting human
  POST /api/supervisor/repush-linear               — retry a blocked Linear push
  GET  /api/supervisor/reviewing-answers           — low-accuracy auto-reply drafts awaiting review
  GET  /api/supervisor/complaint-tickets           — Complaint queue awaiting human
  POST /api/supervisor/close-complaint             — human-confirmed complaint close
  GET  /api/supervisor/ai-cs/status                — knowledge-feedback feature on/configured
  GET  /api/supervisor/ai-cs/skills                — list managed AI 客服 skills
  GET  /api/supervisor/ai-cs/skills/{name}         — skill published files + history
  POST /api/supervisor/ai-cs/skills/{name}/drafts  — create a skill revision draft
  POST /api/supervisor/ai-cs/replay                — re-answer with current/draft skill (test)
  POST /api/supervisor/ai-cs/publish               — publish a skill draft to production
  GET  /api/supervisor/tickets/{id}/escalation-context — golden triple for reflect UI
  GET  /api/supervisor/kb/status                   — 飞书知识库连通状态
  GET  /api/supervisor/kb/search?q=                 — 按问题检索知识库（KB 病因诊断）

All endpoints require role IN ('supervisor', 'admin') — EXCEPT the reflect
workbench group (escalation-pending-diagnosis / ai-cs/* / escalation-context /
diagnosis / reflect), which uses require_knowledge_op（ADR-0016 P5 权限双层：
知识运营管对客 skill，够不到内部编排 skill 与主管修正权）.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsBusinessError, AiCsError
from app.api.deps.auth import AuthedUser, require_knowledge_op, require_supervisor, require_user
from app.api.history_labels import HUB_TYPE_ZH
from app.api.hub_issues import _authorize_hub_handler
from app.core.llm_router import LLMRouterError
from app.core.logging import get_logger
from app.db import get_session
from app.models import AgentDecision, HubIssue, StatusHistory, Ticket
from app.repositories.notification_log import NotificationLogRepository
from app.repositories.status_history import StatusHistoryRepository
from app.services import knowledge_feedback as kf
from app.services.agents.classify import classify_ticket
from app.services.agents.dedup_execute import (
    DedupExecuteError,
    dismiss_dedup_proposal,
    execute_dedup,
    list_pending_dedup_proposals,
)
from app.services.agents.split import (
    SplitError,
    dismiss_split_proposal,
    execute_split,
    list_pending_split_proposals,
    revert_split,
)
from app.services.cascade.supply_sync import (
    SupplySyncError,
    batch_request_supply,
)
from app.services.hub_issues.creator import (
    HubIssueCreateError,
    ensure_hub_issue_for_ticket,
)
from app.services.hub_issues.linear_push import push_hub_issue_to_linear
from app.services.hub_issues.module_owner import consume_module_owner, peek_module_owner
from app.services.hub_issues.op_status import (
    OP_PROCESSING,
    OP_REVIEWING,
    apply_op_status,
    default_owner_from_ticket_handler,
    record_ticket_action,
)
from app.services.ksm.notice_store import NoticeStore
from app.services.ksm.writeback import drain_ksm_outbox
from app.services.supervisor.config_warnings import get_config_warnings
from app.services.supervisor.manual_assign import (
    AssignRequest,
    ManualAssignService,
    TargetUserInvalidError,
)
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


class BatchSupplyBody(BaseModel):
    ticket_ids: list[int] = Field(..., min_length=1, max_length=50)
    note: str = Field(..., min_length=1, max_length=1000)


class BatchSupplyItemOut(BaseModel):
    ticket_id: int
    short_code: str
    success: bool
    message: str


class BatchSupplyResponse(BaseModel):
    results: list[BatchSupplyItemOut]
    enqueued_count: int
    skipped_count: int


class AssignBody(BaseModel):
    ticket_ids: list[int] = Field(..., min_length=1, max_length=50)
    assigned_user_id: int


class AssignItemOut(BaseModel):
    ticket_id: int
    short_code: str
    success: bool
    prev_assigned_user_id: int | None
    message: str


class AssignResponse(BaseModel):
    results: list[AssignItemOut]
    assigned_count: int
    not_found_count: int


# ---- endpoints ------------------------------------------------------------


def _handler_scope(db: Session, user: AuthedUser) -> Any:
    """人工确认闸门行级可见性过滤：主管/admin 返回 None（看全部）；其余角色
    只返回「处理人 = 自己」的 hub——op_handler_user_id 或任一关联 ticket 的
    handler_user_id（与 list_tickets 的 visible_to_user_id、_authorize_hub_handler
    口径一致）。"""
    if user.role in ("supervisor", "admin"):
        return None
    handler_hub_ids = select(Ticket.hub_issue_id).where(
        Ticket.handler_user_id == user.user_id,
        Ticket.hub_issue_id.isnot(None),
    )
    return or_(
        HubIssue.op_handler_user_id == user.user_id,
        HubIssue.id.in_(handler_hub_ids),
    )


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


@router.post("/batch-supply", response_model=BatchSupplyResponse)
def batch_supply_tickets(
    body: BatchSupplyBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> BatchSupplyResponse:
    """批量补充资料：对勾选工单退回提单人补充资料（入 supply outbox，
    KSM/智齿 sender 消费成 supplyKsmOrder）。工单不必已毕业成 hub_issue。"""
    try:
        result = batch_request_supply(
            db,
            body.ticket_ids,
            note=body.note,
            requested_by=f"user:{user.name}",
        )
    except SupplySyncError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info(
        "supervisor_batch_supply",
        ticket_ids=body.ticket_ids,
        enqueued_count=result.enqueued_count,
        skipped_count=result.skipped_count,
        operator_user_id=user.user_id,
    )
    return BatchSupplyResponse(
        results=[
            BatchSupplyItemOut(
                ticket_id=r.ticket_id,
                short_code=r.short_code,
                success=r.success,
                message=r.message,
            )
            for r in result.results
        ],
        enqueued_count=result.enqueued_count,
        skipped_count=result.skipped_count,
    )


@router.post("/assign", response_model=AssignResponse)
def assign_tickets(
    body: AssignBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> AssignResponse:
    try:
        result = ManualAssignService(db).assign(
            AssignRequest(
                ticket_ids=body.ticket_ids,
                assigned_user_id=body.assigned_user_id,
                operator_user_id=user.user_id,
            )
        )
    except TargetUserInvalidError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    logger.info(
        "supervisor_assign",
        ticket_ids=body.ticket_ids,
        assigned_user_id=body.assigned_user_id,
        assigned_count=result.assigned_count,
        operator_user_id=user.user_id,
    )
    return AssignResponse(
        results=[
            AssignItemOut(
                ticket_id=r.ticket_id,
                short_code=r.short_code,
                success=r.success,
                prev_assigned_user_id=r.prev_assigned_user_id,
                message=r.message,
            )
            for r in result.results
        ],
        assigned_count=result.assigned_count,
        not_found_count=result.not_found_count,
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
    # 确认分类时可覆盖产品线/模块（否则继承 ticket 原值）。
    product_line_code: str | None = Field(default=None, max_length=64)
    module: str | None = Field(default=None, max_length=128)


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
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> CreateHubIssueResponse:
    """Graduate a ticket to a hub_issue (manual path, no confidence gate).

    权限：主管/管理员，或该 ticket 的处理人本人（ticket.handler_user_id）。
    Bug_fix/Demand issues are pushed to Linear asynchronously when
    LINEAR_PUSH_ENABLED is on (the push itself re-checks all gates).
    """
    if user.role not in ("supervisor", "admin"):
        t = db.get(Ticket, body.ticket_id)
        if t is None or t.handler_user_id != user.user_id:
            raise HTTPException(
                status_code=403, detail="需要主管/管理员，或本工单处理人才能确认分类"
            )
    try:
        result = ensure_hub_issue_for_ticket(
            body.ticket_id,
            created_by=f"user:{user.name}",
            type_override=body.type,
            product_line_code=body.product_line_code,
            module=body.module,
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


# ---- dedup proposals (D4 第①段) ---------------------------------------------


class DedupTargetOut(BaseModel):
    ticket_id: int
    short_code: str
    title: str | None
    hub_issue_id: int | None  # None → 采纳前需先对目标 create-hub-issue


class DedupProposalItem(BaseModel):
    decision_id: int
    ticket_id: int
    ticket_short_code: str
    ticket_title: str | None
    duplicate_of: DedupTargetOut | None  # None → 目标已删除，只能忽略
    confidence: float
    similarity: float | None  # 召回相似度（top 候选）
    reason: str
    created_at: datetime


class DedupProposalsResponse(BaseModel):
    items: list[DedupProposalItem]


class ExecuteDedupBody(BaseModel):
    decision_id: int


class ExecuteDedupResponse(BaseModel):
    decision_id: int
    ticket_id: int
    duplicate_of_ticket_id: int
    hub_issue_id: int
    hub_issue_short_code: str


class DismissDedupBody(BaseModel):
    decision_id: int
    reason: str | None = Field(default=None, max_length=500)


class DismissDedupResponse(BaseModel):
    decision_id: int


@router.get("/dedup-proposals", response_model=DedupProposalsResponse)
def list_dedup_proposals(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> DedupProposalsResponse:
    """Pending dedup_link proposals awaiting supervisor action."""
    rows = list_pending_dedup_proposals(db, limit=min(limit, 100))
    items = []
    for d, t, target in rows:
        candidates = d.proposal.get("candidates") or []
        top_sim = None
        target_id = d.proposal.get("duplicate_of_ticket_id")
        for c in candidates:
            if c.get("ticket_id") == target_id:
                top_sim = c.get("similarity")
                break
        items.append(
            DedupProposalItem(
                decision_id=d.id,
                ticket_id=t.id,
                ticket_short_code=t.short_code,
                ticket_title=t.title,
                duplicate_of=(
                    DedupTargetOut(
                        ticket_id=target.id,
                        short_code=target.short_code,
                        title=target.title,
                        hub_issue_id=target.hub_issue_id,
                    )
                    if target is not None
                    else None
                ),
                confidence=float(d.proposal.get("confidence") or 0.0),
                similarity=top_sim,
                reason=str(d.proposal.get("reason") or ""),
                created_at=d.created_at,
            )
        )
    return DedupProposalsResponse(items=items)


@router.post("/execute-dedup", response_model=ExecuteDedupResponse)
def execute_dedup_endpoint(
    body: ExecuteDedupBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> ExecuteDedupResponse:
    """Merge the duplicate ticket onto the original's hub_issue."""
    try:
        result = execute_dedup(body.decision_id, executed_by=f"user:{user.name}", db=db)
    except DedupExecuteError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "supervisor_execute_dedup",
        decision_id=body.decision_id,
        ticket_id=result.ticket_id,
        hub_issue_id=result.hub_issue_id,
        operator_user_id=user.user_id,
    )
    return ExecuteDedupResponse(
        decision_id=result.decision_id,
        ticket_id=result.ticket_id,
        duplicate_of_ticket_id=result.duplicate_of_ticket_id,
        hub_issue_id=result.hub_issue_id,
        hub_issue_short_code=result.hub_issue_short_code,
    )


@router.post("/dismiss-dedup", response_model=DismissDedupResponse)
def dismiss_dedup_endpoint(
    body: DismissDedupBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DismissDedupResponse:
    try:
        decision_id = dismiss_dedup_proposal(
            body.decision_id, dismissed_by=f"user:{user.name}", reason=body.reason, db=db
        )
    except DedupExecuteError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return DismissDedupResponse(decision_id=decision_id)


# ---- pending hub_issues / Linear repush (D4 第①段) ---------------------------


class PendingHubIssueItem(BaseModel):
    hub_issue_id: int
    short_code: str
    type: str
    title: str
    assigned_user_id: int | None
    pending_reason: str | None  # latest status_history → pending
    pending_since: datetime | None


class PendingHubIssuesResponse(BaseModel):
    items: list[PendingHubIssueItem]


class RepushLinearBody(BaseModel):
    hub_issue_id: int


class RepushLinearResponse(BaseModel):
    hub_issue_id: int
    pushed: bool
    linear_identifier: str | None
    # 仍失败时：最新的 pending 原因（原因不变则为原原因）
    pending_reason: str | None


@router.get("/pending-hub-issues", response_model=PendingHubIssuesResponse)
def list_pending_hub_issues(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> PendingHubIssuesResponse:
    """hub_issues whose Linear push is blocked (status='pending') + why."""
    hubs = (
        db.query(HubIssue)
        .filter(HubIssue.deleted_at.is_(None), HubIssue.status == "pending")
        .order_by(HubIssue.updated_at.desc())
        .limit(min(limit, 100))
        .all()
    )
    items = []
    for h in hubs:
        last_pending = (
            db.query(StatusHistory)
            .filter_by(entity_type="hub_issue", entity_id=h.id, to_status="pending")
            .order_by(StatusHistory.id.desc())
            .first()
        )
        items.append(
            PendingHubIssueItem(
                hub_issue_id=h.id,
                short_code=h.short_code,
                type=h.type,
                title=h.title,
                assigned_user_id=h.assigned_user_id,
                pending_reason=last_pending.reason if last_pending else None,
                pending_since=last_pending.changed_at if last_pending else None,
            )
        )
    return PendingHubIssuesResponse(items=items)


# ---- reviewing-answers 主管审核队列 (D_review 低置信答复) ----------------------


class ReviewingAnswerItem(BaseModel):
    hub_issue_id: int
    short_code: str
    title: str
    question: str | None
    draft_reply: str | None
    accuracy: int | None
    accuracy_reason: str | None


class ReviewingAnswersResponse(BaseModel):
    items: list[ReviewingAnswerItem]


@router.get("/reviewing-answers", response_model=ReviewingAnswersResponse)
def list_reviewing_answers(
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> ReviewingAnswersResponse:
    """Operation hubs awaiting human review of a low-accuracy auto-reply draft.

    行级可见性：主管/admin 看全部；处理人只看处理人=自己的（_handler_scope）。
    """
    q = db.query(HubIssue).filter(
        HubIssue.deleted_at.is_(None),
        HubIssue.type == "Operation",
        HubIssue.op_status == "reviewing",
    )
    scope = _handler_scope(db, user)
    if scope is not None:
        q = q.filter(scope)
    hubs = q.order_by(HubIssue.op_status_changed_at.desc()).limit(min(limit, 100)).all()
    items = []
    for h in hubs:
        dec = (
            db.query(AgentDecision)
            .filter_by(subject_type="hub_issue", subject_id=h.id, decision_type="auto_reply")
            .order_by(AgentDecision.id.desc())
            .first()
        )
        prop: dict[str, Any] = (dec.proposal if dec else {}) or {}
        items.append(
            ReviewingAnswerItem(
                hub_issue_id=h.id,
                short_code=h.short_code,
                title=h.title,
                question=prop.get("question"),
                draft_reply=h.reply_content,
                accuracy=prop.get("accuracy"),
                accuracy_reason=prop.get("reason"),
            )
        )
    return ReviewingAnswersResponse(items=items)


class EscalationPendingItem(BaseModel):
    ticket_id: int
    short_code: str
    title: str | None
    dissatisfaction: str | None
    is_ai_cs_escalation: bool
    created_at: datetime


class EscalationPendingResponse(BaseModel):
    items: list[EscalationPendingItem]


@router.get(
    "/escalation-pending-diagnosis",
    response_model=EscalationPendingResponse,
)
def list_escalation_pending_diagnosis(
    _user: AuthedUser = Depends(require_knowledge_op),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> EscalationPendingResponse:
    """AI 客服 escalation 工单 + 已标记诊断的运营单中尚未做出病因判定的（反思诊断
    工作台待处理队列）。

    diagnosis 存在 source_payload['ai_cs']['diagnosis']（JSON），跨库无法列过滤
    —— 与召回同一套「量小，Python 侧过滤」剧本（当前量级足够）。

    ADR-0016：反思只对 Operation 有意义（Bug/Demand 走 Linear，投诉走人工）——
    predicted_type 过滤 Operation；NULL（分类失败/未跑）也保留进队列，
    避免 LLM 挂掉时新失败悄悄漏出人工视野。
    """
    rows = (
        db.query(Ticket)
        .filter(
            Ticket.deleted_at.is_(None),
            or_(Ticket.source_code == "ai_cs", Ticket.diagnosis_flagged_at.isnot(None)),
            or_(Ticket.predicted_type == "Operation", Ticket.predicted_type.is_(None)),
        )
        .order_by(Ticket.created_at.desc())
        .limit(min(limit, 100) * 3)  # 过采样，Python 过滤后再截断
        .all()
    )
    items = []
    for t in rows:
        ai = (t.source_payload or {}).get("ai_cs") or {}
        diagnosis = ai.get("diagnosis")
        if diagnosis and diagnosis.get("cause"):
            continue
        items.append(
            EscalationPendingItem(
                ticket_id=t.id,
                short_code=t.short_code,
                title=t.title,
                dissatisfaction=ai.get("dissatisfaction"),
                is_ai_cs_escalation=t.source_code == "ai_cs",
                created_at=t.created_at,
            )
        )
        if len(items) >= min(limit, 100):
            break
    return EscalationPendingResponse(items=items)


class ReflectTicketItem(BaseModel):
    id: int
    short_code: str
    title: str | None
    status: str
    created_at: datetime


class ReflectTicketsResponse(BaseModel):
    items: list[ReflectTicketItem]
    total: int


@router.get("/reflect-tickets", response_model=ReflectTicketsResponse)
def list_reflect_tickets(
    _user: AuthedUser = Depends(require_knowledge_op),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> ReflectTicketsResponse:
    """反思诊断工作台左侧工单浏览列表：ai_cs escalation ∪ 已标记诊断的运营工单
    （不按 diagnosis 是否完成过滤——供回看已诊断的工单）。"""
    q = db.query(Ticket).filter(
        Ticket.deleted_at.is_(None),
        or_(Ticket.source_code == "ai_cs", Ticket.diagnosis_flagged_at.isnot(None)),
    )
    total = q.count()
    rows = q.order_by(Ticket.created_at.desc()).limit(min(limit, 100)).all()
    return ReflectTicketsResponse(
        items=[
            ReflectTicketItem(
                id=t.id,
                short_code=t.short_code,
                title=t.title,
                status=t.status,
                created_at=t.created_at,
            )
            for t in rows
        ],
        total=total,
    )


# ---- 投诉队列 (ADR-0016 P2d) --------------------------------------------------


class ComplaintTicketItem(BaseModel):
    ticket_id: int
    short_code: str
    title: str | None
    source_code: str | None
    confidence: float | None
    created_at: datetime


class ComplaintTicketsResponse(BaseModel):
    items: list[ComplaintTicketItem]


class CloseComplaintBody(BaseModel):
    ticket_id: int
    reason: str | None = Field(default=None, max_length=500)


class CloseComplaintResponse(BaseModel):
    ticket_id: int
    status: str


_TICKET_TERMINAL_STATUSES = ("done", "closed", "rejected", "superseded")


@router.get("/complaint-tickets", response_model=ComplaintTicketsResponse)
def list_complaint_tickets(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> ComplaintTicketsResponse:
    """投诉工单人工队列（ADR-0016：Complaint 停 ticket 层，绝不自动毕业）。

    人工两条出路：纯情绪投诉 → close-complaint 关闭；投诉裹着真问题 →
    create-hub-issue 带 type 覆盖转型毕业（转毕业后 hub_issue_id 落值，
    自动离开本队列）。
    """
    rows = (
        db.query(Ticket)
        .filter(
            Ticket.deleted_at.is_(None),
            Ticket.predicted_type == "Complaint",
            Ticket.hub_issue_id.is_(None),
            Ticket.status.notin_(_TICKET_TERMINAL_STATUSES),
        )
        .order_by(Ticket.created_at.asc())
        .limit(min(limit, 100))
        .all()
    )
    return ComplaintTicketsResponse(
        items=[
            ComplaintTicketItem(
                ticket_id=t.id,
                short_code=t.short_code,
                title=t.title,
                source_code=t.source_code,
                confidence=(
                    float(t.predicted_confidence) if t.predicted_confidence is not None else None
                ),
                created_at=t.created_at,
            )
            for t in rows
        ]
    )


@router.post("/close-complaint", response_model=CloseComplaintResponse)
def close_complaint_endpoint(
    body: CloseComplaintBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> CloseComplaintResponse:
    """人工确认关闭投诉工单（判定为纯安抚场景、无需转出口时）。"""
    t = db.get(Ticket, body.ticket_id)
    if t is None or t.deleted_at is not None:
        raise HTTPException(status_code=404, detail="ticket not found")
    if t.predicted_type != "Complaint":
        raise HTTPException(
            status_code=409, detail=f"非投诉工单（predicted_type={t.predicted_type}）"
        )
    if t.status in _TICKET_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"工单已是终态（{t.status}）")
    prev = t.status
    t.status = "closed"
    db.add(
        StatusHistory(
            entity_type="ticket",
            entity_id=t.id,
            from_status=prev,
            to_status="closed",
            changed_by=f"user:{user.name}",
            reason=body.reason or "投诉人工确认关闭",
        )
    )
    db.commit()
    logger.info(
        "supervisor_close_complaint",
        ticket_id=t.id,
        from_status=prev,
        operator_user_id=user.user_id,
    )
    return CloseComplaintResponse(ticket_id=t.id, status="closed")


@router.post("/repush-linear", response_model=RepushLinearResponse)
def repush_linear_endpoint(
    body: RepushLinearBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> RepushLinearResponse:
    """Retry a blocked Linear push (e.g. after the assignee joined Linear and
    sync-from-linear refreshed the mapping), or push for the first time when a
    hub graduated as Bug_fix/Demand but was never actually pushed (e.g. type
    was changed via PATCH /attributes, which never pushes). Synchronous — the
    caller wants to see the outcome immediately. 权限放宽到本工单处理人本人
    （_authorize_hub_handler），不再局限主管——处理人在工单详情页自助重推。"""
    _authorize_hub_handler(db, body.hub_issue_id, user)
    hub = db.get(HubIssue, body.hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="hub_issue not found")
    if hub.linear_uuid is not None:
        raise HTTPException(status_code=409, detail=f"already pushed as {hub.linear_identifier}")
    result = push_hub_issue_to_linear(hub.id, db)
    db.refresh(hub)
    pending_reason: str | None = None
    if result is None:
        last_pending = (
            db.query(StatusHistory)
            .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="pending")
            .order_by(StatusHistory.id.desc())
            .first()
        )
        pending_reason = last_pending.reason if last_pending else "推送未执行（检查开关/类型）"
    logger.info(
        "supervisor_repush_linear",
        hub_issue_id=hub.id,
        pushed=result is not None,
        operator_user_id=user.user_id,
    )
    return RepushLinearResponse(
        hub_issue_id=hub.id,
        pushed=result is not None,
        linear_identifier=result.linear_identifier if result else None,
        pending_reason=pending_reason,
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


# ---- KSM 回写 drain (D4 第②段) ---------------------------------------------


class DrainKsmWritebackResponse(BaseModel):
    enabled: bool
    dry_run: bool
    scanned: int
    sent: int
    skipped: int
    failed: int
    deferred: int
    errors: list[str]


@router.post("/drain-ksm-writeback", response_model=DrainKsmWritebackResponse)
def drain_ksm_writeback_endpoint(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DrainKsmWritebackResponse:
    """Manually run one KSM outbox drain pass. Respects ksm_writeback_enabled /
    _dry_run — a supervisor uses this to flush pending回写 on demand and see the
    outcome, rather than waiting for the 2-min beat."""
    from app.config import get_settings

    settings = get_settings()
    notice_store: NoticeStore | None = None
    try:
        notice_store = NoticeStore(redis_url=settings.redis_url)
    except Exception:
        logger.warning("ksm_writeback_manual_no_notice_store")
    report = drain_ksm_outbox(db, notice_store=notice_store, settings=settings)
    logger.info(
        "supervisor_drain_ksm_writeback",
        operator_user_id=user.user_id,
        scanned=report.scanned,
        sent=report.sent,
        failed=report.failed,
    )
    return DrainKsmWritebackResponse(
        enabled=settings.ksm_writeback_enabled,
        dry_run=settings.ksm_writeback_dry_run,
        scanned=report.scanned,
        sent=report.sent,
        skipped=report.skipped,
        failed=report.failed,
        deferred=report.deferred,
        errors=report.errors[:20],
    )


class DrainZhichiWritebackResponse(BaseModel):
    enabled: bool
    dry_run: bool
    scanned: int
    sent: int
    skipped: int
    failed: int
    deferred: int
    errors: list[str]


@router.post("/drain-zhichi-writeback", response_model=DrainZhichiWritebackResponse)
def drain_zhichi_writeback_endpoint(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DrainZhichiWritebackResponse:
    """手动跑一轮智齿 outbox drain。尊重 zhichi_writeback_enabled / _dry_run——
    主管按需 flush pending 回写并立即看结果，不必等 2min beat。"""
    from app.config import get_settings
    from app.services.zhichi.writeback import drain_zhichi_outbox

    settings = get_settings()
    report = drain_zhichi_outbox(db, settings=settings)
    logger.info(
        "supervisor_drain_zhichi_writeback",
        operator_user_id=user.user_id,
        scanned=report.scanned,
        sent=report.sent,
        failed=report.failed,
    )
    return DrainZhichiWritebackResponse(
        enabled=settings.zhichi_writeback_enabled,
        dry_run=settings.zhichi_writeback_dry_run,
        scanned=report.scanned,
        sent=report.sent,
        skipped=report.skipped,
        failed=report.failed,
        deferred=report.deferred,
        errors=report.errors[:20],
    )


# ---- 附件异步流水线 drain (ADR-0016 附件闸道) -------------------------------


class DrainAttachmentsResponse(BaseModel):
    scanned: int
    extracted: int
    skipped: int
    failed: int


@router.post("/drain-attachments", response_model=DrainAttachmentsResponse)
def drain_attachments_endpoint(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DrainAttachmentsResponse:
    """手动跑一轮附件流水线 drain（download → MinIO → vision OCR）。尊重
    attachment_pipeline_enabled / _dry_run——主管按需 flush 队列，不必等 5min beat。"""
    from app.services.attachments.pipeline import drain_pending_attachments

    report = drain_pending_attachments(db)
    db.commit()
    logger.info(
        "supervisor_drain_attachments",
        operator_user_id=user.user_id,
        scanned=report.scanned,
        extracted=report.extracted,
        failed=report.failed,
    )
    return DrainAttachmentsResponse(
        scanned=report.scanned,
        extracted=report.extracted,
        skipped=report.skipped,
        failed=report.failed,
    )


# ---- Phase 1 知识反哺闭环：AI 客服 skill 管理 + replay ----------------------
#
# 主管从 escalation 工单反思 → 改 AI 客服 skill draft → replay 试跑对比旧/新答复
# → 满意则 publish。全部 require_supervisor，全部经 knowledge_feedback_enabled 门控。


class AiCsStatusResponse(BaseModel):
    enabled: bool
    configured: bool  # appid/app_key 是否齐全
    managed_skills: list[str]


class SkillFileModel(BaseModel):
    filename: str
    filepath: str
    content: str | None = None


class SkillSummaryModel(BaseModel):
    skill_name: str
    published_version: str
    operator: str
    updated_at: str
    files: list[SkillFileModel]


class SkillVersionModel(BaseModel):
    version: str
    status: str
    operator: str
    reason: str
    created_at: str


class SkillDetailModel(BaseModel):
    skill_name: str
    published_version: str
    published_operator: str
    published_reason: str
    published_files: list[SkillFileModel]
    history: list[SkillVersionModel]


class CreateDraftBody(BaseModel):
    files: list[SkillFileModel] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)


class CreateDraftResponse(BaseModel):
    version: str


class ReplayBody(BaseModel):
    session_id: str | None = None
    question: str | None = None
    skill: str | None = None
    skill_draft_version: str | None = None
    use_latest_knowledge: bool = True


class ReplayResponse(BaseModel):
    answer: str
    cited_knowledge: list[dict[str, Any]]
    skills_used: list[str]
    trace_id: str


class PublishBody(BaseModel):
    skill_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    # 若从某 escalation 工单发起，回填审计（工单 status_history 记录本次反哺发布）
    ticket_id: int | None = None


class PublishResponse(BaseModel):
    skill_name: str
    version: str
    published: bool


class EscalationContextResponse(BaseModel):
    is_escalation: bool
    ticket_id: int
    session_id: str | None = None
    is_ai_cs_escalation: bool = True
    original_question: str = ""
    ai_answer: str = ""
    dissatisfaction: str = ""
    # 反哺扩展（AI 客服接口1 扩展载荷；老工单为空列表）
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    cited_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    # 反思诊断工作台：主管病因判定 + LLM 反思推断缓存
    diagnosis: dict[str, Any] | None = None
    reflection: dict[str, Any] | None = None


class DiagnosisBody(BaseModel):
    # 旧单值（兼容）；新客户端用 causes 集合（ADR-0016 决策 6 多病因）
    cause: str | None = None  # skill | knowledge | retrieval
    causes: list[str] | None = None  # 主次排序集合；与 cause 同给时 causes 优先
    # 修复清单勾选状态：{cause: done}——只改勾选、不改集合时也走本端点
    checklist_done: dict[str, bool] | None = None
    correct_answer: str | None = Field(default=None, max_length=4000)


class DiagnosisResponse(BaseModel):
    ticket_id: int
    diagnosis: dict[str, Any] | None


class ReflectResponse(BaseModel):
    ticket_id: int
    reflection: dict[str, Any]


def _ai_cs_http_error(e: AiCsError) -> HTTPException:
    """Translate adapter exceptions to HTTP. Business (bad version / not
    managed) → 400; network/auth/unavailable → 502."""
    if isinstance(e, AiCsBusinessError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=502, detail=f"AI 客服 不可用：{e}")


# ---- 飞书知识库检索（ADR-0016 P3 反思闭环 · knowledge/retrieval 病因诊断依据）----


class KbStatusResponse(BaseModel):
    configured: bool  # 配了 FEISHU_WIKI_SPACE_ID
    space_id: str
    doc_count: int | None
    error: str | None  # 遍历失败原因（如权限/网络）；None 表示正常


class KbHitItem(BaseModel):
    node_token: str
    title: str
    snippet: str
    url: str
    score: float
    char_count: int


class KbSearchResponse(BaseModel):
    query: str
    hits: list[KbHitItem]


@router.get("/kb/status", response_model=KbStatusResponse)
def kb_status_endpoint(
    _user: AuthedUser = Depends(require_knowledge_op),
) -> KbStatusResponse:
    """知识库连通状态——UI 据此显示「知识库核对」面板或降级提示。"""
    from app.services.knowledge_feedback import kb_search as kb

    st = kb.kb_status()
    return KbStatusResponse(
        configured=st.configured, space_id=st.space_id, doc_count=st.doc_count, error=st.error
    )


@router.get("/kb/search", response_model=KbSearchResponse)
def kb_search_endpoint(
    q: str,
    _user: AuthedUser = Depends(require_knowledge_op),
    limit: int = 5,
    force: bool = False,
) -> KbSearchResponse:
    """按失败问题检索飞书知识库。空结果 = 检索缺失（retrieval 病因）；
    命中条目供人工判断「知识对不对/AI 用没用好」。force=1 强刷缓存（KB 改完复查）。"""
    from app.services.knowledge_feedback import kb_search as kb

    try:
        hits = kb.search_kb(q, limit=min(max(limit, 1), 20), force=force)
    except kb.KbDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"知识库检索失败：{e}") from e
    return KbSearchResponse(
        query=q,
        hits=[
            KbHitItem(
                node_token=h.doc.node_token,
                title=h.doc.title,
                snippet=h.snippet,
                url=h.doc.url,
                score=h.score,
                char_count=h.doc.char_count,
            )
            for h in hits
        ],
    )


@router.get("/ai-cs/status", response_model=AiCsStatusResponse)
def ai_cs_status_endpoint(
    _user: AuthedUser = Depends(require_knowledge_op),
) -> AiCsStatusResponse:
    """Whether the knowledge-feedback feature is on + configured — the UI hides
    the reflect panel when off."""
    from app.config import get_settings

    settings = get_settings()
    managed = [s.strip() for s in (settings.ai_cs_managed_skills or "").split(",") if s.strip()]
    return AiCsStatusResponse(
        enabled=bool(settings.knowledge_feedback_enabled),
        configured=bool(settings.ai_cs_app_id and settings.ai_cs_app_key),
        managed_skills=managed,
    )


@router.get("/ai-cs/skills", response_model=list[SkillSummaryModel])
def ai_cs_list_skills_endpoint(
    _user: AuthedUser = Depends(require_knowledge_op),
) -> list[SkillSummaryModel]:
    from app.config import get_settings

    try:
        client = kf.build_client(get_settings())
    except kf.KnowledgeFeedbackDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        skills = client.list_skills()
    except AiCsError as e:
        raise _ai_cs_http_error(e) from e
    finally:
        client.close()
    return [
        SkillSummaryModel(
            skill_name=s.skill_name,
            published_version=s.published_version,
            operator=s.operator,
            updated_at=s.updated_at,
            files=[SkillFileModel(filename=f.filename, filepath=f.filepath) for f in s.files],
        )
        for s in skills
    ]


@router.get("/ai-cs/skills/{name}", response_model=SkillDetailModel)
def ai_cs_get_skill_endpoint(
    name: str,
    _user: AuthedUser = Depends(require_knowledge_op),
) -> SkillDetailModel:
    from app.config import get_settings

    try:
        client = kf.build_client(get_settings())
    except kf.KnowledgeFeedbackDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        d = client.get_skill(name)
    except AiCsError as e:
        raise _ai_cs_http_error(e) from e
    finally:
        client.close()
    return SkillDetailModel(
        skill_name=d.skill_name,
        published_version=d.published_version,
        published_operator=d.published_operator,
        published_reason=d.published_reason,
        published_files=[
            SkillFileModel(filename=f.filename, filepath=f.filepath, content=f.content)
            for f in d.published_files
        ],
        history=[
            SkillVersionModel(
                version=v.version,
                status=v.status,
                operator=v.operator,
                reason=v.reason,
                created_at=v.created_at,
            )
            for v in d.history
        ],
    )


@router.post("/ai-cs/skills/{name}/drafts", response_model=CreateDraftResponse)
def ai_cs_create_draft_endpoint(
    name: str,
    body: CreateDraftBody,
    user: AuthedUser = Depends(require_knowledge_op),
) -> CreateDraftResponse:
    """Create a skill draft off the current published version. Empty files
    inherits published; non-empty upserts. Draft is NOT live until published."""
    from app.config import get_settings

    try:
        client = kf.build_client(get_settings())
    except kf.KnowledgeFeedbackDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    files = [f.model_dump(exclude_none=True) for f in body.files]
    try:
        version = client.create_draft(
            name, files=files, operator=f"user:{user.name}", reason=body.reason
        )
    except AiCsError as e:
        raise _ai_cs_http_error(e) from e
    finally:
        client.close()
    logger.info(
        "knowledge_feedback_create_draft",
        skill=name,
        version=version,
        operator_user_id=user.user_id,
    )
    return CreateDraftResponse(version=version)


@router.post("/ai-cs/replay", response_model=ReplayResponse)
def ai_cs_replay_endpoint(
    body: ReplayBody,
    user: AuthedUser = Depends(require_knowledge_op),
) -> ReplayResponse:
    """Re-answer a question with the current or a draft skill + latest
    knowledge — the reflect/test button. Pass skill_draft_version to test an
    unpublished draft without touching production."""
    if not body.session_id and not body.question:
        raise HTTPException(status_code=422, detail="必须提供 session_id 或 question")
    from app.config import get_settings

    try:
        client = kf.build_client(get_settings())
    except kf.KnowledgeFeedbackDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        result = client.replay(
            session_id=body.session_id,
            question=body.question,
            skill=body.skill,
            use_latest_knowledge=body.use_latest_knowledge,
            skill_draft_version=body.skill_draft_version,
        )
    except AiCsError as e:
        raise _ai_cs_http_error(e) from e
    finally:
        client.close()
    logger.info(
        "knowledge_feedback_replay",
        skill=body.skill,
        draft=body.skill_draft_version,
        trace_id=result.trace_id,
        operator_user_id=user.user_id,
    )
    return ReplayResponse(
        answer=result.answer,
        cited_knowledge=result.cited_knowledge,
        skills_used=result.skills_used,
        trace_id=result.trace_id,
    )


@router.post("/ai-cs/publish", response_model=PublishResponse)
def ai_cs_publish_endpoint(
    body: PublishBody,
    user: AuthedUser = Depends(require_knowledge_op),
    db: Session = Depends(get_session),
) -> PublishResponse:
    """Publish a skill draft to production. If ticket_id is given, record a
    knowledge-revision audit row on that escalation ticket."""
    from app.config import get_settings

    try:
        client = kf.build_client(get_settings())
    except kf.KnowledgeFeedbackDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        client.publish_draft(body.skill_name, body.version)
    except AiCsError as e:
        raise _ai_cs_http_error(e) from e
    finally:
        client.close()
    if body.ticket_id is not None:
        kf.record_publish_audit(
            db,
            ticket_id=body.ticket_id,
            skill_name=body.skill_name,
            version=body.version,
            operator=f"user:{user.name}",
        )
        db.commit()
    logger.info(
        "knowledge_feedback_publish",
        skill=body.skill_name,
        version=body.version,
        ticket_id=body.ticket_id,
        operator_user_id=user.user_id,
    )
    return PublishResponse(skill_name=body.skill_name, version=body.version, published=True)


def _authorize_escalation_ticket(db: Session, ticket_id: int, user: AuthedUser) -> None:
    """诊断/反思三端点授权：knowledge_op 及以上角色直接放行（真实 ai_cs
    escalation 场景仍是知识运营主战场）；否则要求是该 ticket 关联 hub 的处理人
    ——这条放宽只覆盖 op_status=reviewing 处理人自助诊断场景（真实 ai_cs
    escalation 多数尚无 hub_issue_id，会被 _authorize_hub_handler 挡下，
    与期望一致：非知识运营不该碰真实客户投诉的诊断）。"""
    if user.role in ("knowledge_op", "supervisor", "admin"):
        return
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.hub_issue_id is None:
        raise HTTPException(status_code=403, detail="需要知识运营/主管/管理员权限")
    _authorize_hub_handler(db, ticket.hub_issue_id, user, base_roles=("supervisor", "admin"))


@router.get("/tickets/{ticket_id}/escalation-context", response_model=EscalationContextResponse)
def ai_cs_escalation_context_endpoint(
    ticket_id: int,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> EscalationContextResponse:
    """The golden triple (原问题/AI答复/不满) for an ai_cs escalation ticket, so
    the reflect UI can seed the comparison. is_escalation=false for non-ai_cs
    tickets (UI hides the panel).

    权限放宽到 reviewing 态处理人本人（_authorize_escalation_ticket）：处理人
    自助诊断 AI 答复因打分未过被转人工审核的场景，无需知识运营代操作。"""
    _authorize_escalation_ticket(db, ticket_id, user)
    ctx = kf.load_escalation_context(db, ticket_id)
    if ctx is None:
        return EscalationContextResponse(is_escalation=False, ticket_id=ticket_id)
    return EscalationContextResponse(
        is_escalation=True,
        ticket_id=ctx.ticket_id,
        session_id=ctx.session_id,
        is_ai_cs_escalation=ctx.is_ai_cs_escalation,
        original_question=ctx.original_question,
        ai_answer=ctx.ai_answer,
        dissatisfaction=ctx.dissatisfaction,
        conversation=ctx.conversation,
        cited_knowledge=ctx.cited_knowledge,
        skills_used=ctx.skills_used,
        diagnosis=ctx.diagnosis,
        reflection=ctx.reflection,
    )


@router.put("/tickets/{ticket_id}/diagnosis", response_model=DiagnosisResponse)
def save_diagnosis_endpoint(
    ticket_id: int,
    body: DiagnosisBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> DiagnosisResponse:
    """Persist the supervisor's cause verdict (skill/knowledge/retrieval) and
    the human-verified correct answer for an escalation ticket.

    权限放宽到 reviewing 态处理人本人（_authorize_escalation_ticket）。"""
    _authorize_escalation_ticket(db, ticket_id, user)
    try:
        diagnosis = kf.save_diagnosis(
            db,
            ticket_id,
            cause=body.cause,
            causes=body.causes,
            checklist_done=body.checklist_done,
            correct_answer=body.correct_answer,
            operator=f"user:{user.user_id}",
        )
    except kf.NotEscalationError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    return DiagnosisResponse(ticket_id=ticket_id, diagnosis=diagnosis)


@router.post("/tickets/{ticket_id}/reflect", response_model=ReflectResponse)
def run_reflect_endpoint(
    ticket_id: int,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ReflectResponse:
    """Run the LLM reflect agent (3-step audit → inferred cause) over the
    escalation context. Synchronous — supervisor watches the result. The
    result is cached on the ticket; rerunning overwrites.

    权限放宽到 reviewing 态处理人本人（_authorize_escalation_ticket）。对
    op_status=reviewing 的工单（AI 答复因打分未过转人工审核），若 LLM 给出
    revised_answer，自动回填到 hub.reply_content 草稿——处理人无需额外「采纳」
    动作，编辑/确认后仍走既有「提交答复」发出。"""
    from app.services.agents.operation_answer import apply_reflect_draft
    from app.services.knowledge_feedback import reflect as rf

    _authorize_escalation_ticket(db, ticket_id, user)
    ctx = kf.load_escalation_context(db, ticket_id)
    if ctx is None:
        raise HTTPException(
            status_code=404, detail=f"ticket {ticket_id} 不是 AI 客服 escalation 工单"
        )
    correct_answer = None
    if ctx.diagnosis and ctx.diagnosis.get("correct_answer"):
        correct_answer = str(ctx.diagnosis["correct_answer"])
    try:
        result = rf.run_reflect(
            question=ctx.original_question,
            ai_answer=ctx.ai_answer,
            dissatisfaction=ctx.dissatisfaction,
            conversation=ctx.conversation,
            cited_knowledge=ctx.cited_knowledge,
            correct_answer=correct_answer,
        )
    except rf.ReflectError as e:
        raise HTTPException(status_code=502, detail=f"反思推断解析失败：{e}") from e
    except LLMRouterError as e:
        raise HTTPException(status_code=503, detail=f"LLM 不可用：{e}") from e
    reflection = result.as_payload()
    kf.save_reflection(db, ticket_id, reflection)
    ticket = db.get(Ticket, ticket_id)
    if ticket is not None and result.revised_answer:
        hub = kf.reviewing_hub_for_ticket(db, ticket)
        if hub is not None:
            apply_reflect_draft(db, hub, content=result.revised_answer)
    db.commit()
    logger.info(
        "escalation_reflect_done",
        ticket_id=ticket_id,
        cause=result.cause,
        confidence=result.confidence,
        model=result.model,
        cost_usd=result.cost_usd,
        operator_user_id=user.user_id,
    )
    return ReflectResponse(ticket_id=ticket_id, reflection=reflection)


# ---- 待确认分类队列 + 三动作（研发类推 Linear 前人工确认闸门）------------------


class PendingClassificationItem(BaseModel):
    hub_issue_id: int
    short_code: str
    type: str
    title: str
    body: str | None
    predicted_type: str | None
    confidence: float | None
    reason: str | None
    # 模块归类审核（module_resolve 已写好的生效值 + AI 原始判定，供处理人对比确认）
    product_line_code: str | None
    module: str | None
    predicted_module: str | None
    predicted_module_confidence: float | None


class PendingClassificationResponse(BaseModel):
    items: list[PendingClassificationItem]


@router.get("/pending-classification", response_model=PendingClassificationResponse)
def list_pending_classification(
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> PendingClassificationResponse:
    """全类型 status=pending_review 待确认分类队列（闸门①）。

    闸门①开启后 Operation/Bug_fix/Demand/Internal_task 毕业后一律先停
    pending_review，故此队列不再只挑研发类。注意 pending_review 与既有
    status='pending'（Linear 推送失败待人工，pending-hub-issues 端点消费）
    是不同队列、不同状态值。

    行级可见性：主管/admin 看全部；处理人只看处理人=自己的（_handler_scope）。
    """
    q = db.query(HubIssue).filter(
        HubIssue.deleted_at.is_(None),
        HubIssue.status == "pending_review",
    )
    scope = _handler_scope(db, user)
    if scope is not None:
        q = q.filter(scope)
    hubs = q.order_by(HubIssue.updated_at.desc()).limit(min(limit, 100)).all()
    items: list[PendingClassificationItem] = []
    for h in hubs:
        reason: str | None = None
        conf: float | None = None
        tk = (
            db.query(Ticket)
            .filter(Ticket.hub_issue_id == h.id, Ticket.deleted_at.is_(None))
            .first()
        )
        if tk is not None:
            dec = (
                db.query(AgentDecision)
                .filter(
                    AgentDecision.subject_type == "ticket",
                    AgentDecision.subject_id == tk.id,
                    AgentDecision.decision_type == "classify_type",
                )
                .order_by(AgentDecision.id.desc())
                .first()
            )
            if dec and dec.proposal:
                reason = dec.proposal.get("reason")
                conf = dec.proposal.get("confidence")
        items.append(
            PendingClassificationItem(
                hub_issue_id=h.id,
                short_code=h.short_code,
                type=h.type,
                title=h.title,
                body=h.canonical_body,
                predicted_type=h.type,
                confidence=conf,
                reason=reason,
                product_line_code=h.product_line_code,
                module=h.module,
                predicted_module=tk.predicted_module if tk is not None else None,
                predicted_module_confidence=(
                    float(tk.predicted_module_confidence)
                    if tk is not None and tk.predicted_module_confidence is not None
                    else None
                ),
            )
        )
    return PendingClassificationResponse(items=items)


class ConfirmClassificationBody(BaseModel):
    hub_issue_id: int
    # 审核时可修正模块归类（不传则保留 AI 判定的现有值）；确认前必须非空。
    product_line_code: str | None = None
    module: str | None = Field(None, max_length=128)


class ReclassifyBody(BaseModel):
    hub_issue_id: int
    new_type: str = Field(..., pattern="^(Operation|Bug_fix|Demand|Internal_task|Complaint)$")
    reason: str = Field("", max_length=500)


class DismissClassificationBody(BaseModel):
    hub_issue_id: int
    reason: str = Field("", max_length=500)


class ClassificationActionResponse(BaseModel):
    hub_issue_id: int
    status: str
    type: str


def _get_pending_review_hub(db: Session, hub_issue_id: int) -> HubIssue:
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_issue_id} not found")
    if hub.status != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"hub {hub.short_code} status={hub.status!r} 非 pending_review，不可操作",
        )
    return hub


def _schedule_ksm_takeover(
    background_tasks: BackgroundTasks, db: Session, hub_issue_id: int
) -> None:
    """人工审核确认分类后的接管兜底重试（主路径已改回入库/派单后立即接管，见
    webhooks.py::_run_ksm_takeover）。已接管的 ticket 在 trigger 内部幂等跳过，
    故这里只补偿入库瞬间接管失败的情况。一个 hub 可能挂多条 ticket（dedup 合并），
    逐条判断来源，非 KSM 直接在 trigger 内部跳过。"""
    from app.services.ksm.takeover import trigger_ksm_takeover_after_review

    ticket_ids = [
        tid
        for (tid,) in db.query(Ticket.id).filter(
            Ticket.hub_issue_id == hub_issue_id, Ticket.deleted_at.is_(None)
        )
    ]
    for tid in ticket_ids:
        background_tasks.add_task(trigger_ksm_takeover_after_review, tid)


def _require_module_assigned(hub: HubIssue) -> None:
    """确认分类前模块归类不能为空（用户新规则）：module 为空或落在兜底模块时拒绝，
    要求先补齐模块归类再确认。兜底模块（module_resolve 四级回退最后一档）视为
    「未真正归类」，不算已确认。"""
    from app.config import get_settings

    settings = get_settings()
    if not (hub.module or "").strip() or hub.module == settings.module_fallback_module:
        raise HTTPException(
            status_code=422,
            detail=f"hub {hub.short_code} 模块归类为空或仍是兜底模块「{hub.module}」，"
            "请先补齐模块归类再确认分类",
        )


def _get_reclassifiable_hub(db: Session, hub_issue_id: int) -> HubIssue:
    """reclassify 可作用的 hub：待确认分类（pending_review），或【处理中/草稿待审的
    Operation】（运营处理中/AI 已生成待审草稿时发现是需求/Bug 需转研发推 Linear）。
    已答复/已关闭 Operation = 处理完成，不可转（业务规则：已答复不转 Linear）。
    其他状态一律拒。"""
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_issue_id} not found")
    if hub.status == "pending_review":
        return hub
    if hub.type == "Operation" and hub.op_status in (OP_PROCESSING, OP_REVIEWING):
        return hub
    raise HTTPException(
        status_code=409,
        detail=(
            f"hub {hub.short_code} status={hub.status!r} op_status={hub.op_status!r} "
            "不可改判（仅待确认分类、或处理中/草稿待审的运营工单可改判转研发）"
        ),
    )


@router.post("/confirm-classification", response_model=ClassificationActionResponse)
def confirm_classification(
    body: ConfirmClassificationBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ClassificationActionResponse:
    """确认分类无误 → 按 hub.type 分流。权限放宽到处理人本人（_authorize_hub_handler）：

    - Operation → created + op_status=processing/agent（回到自动答复链，由
      Celery drain 扫描触发，此处不直接调答复）。
    - Bug_fix/Demand → 按模块负责人是否确定分流：确定 → created + 推 Linear；
      不确定 → pending_linear_review 待处理人确认（工作台选人推送）。
    - Internal_task → created（无外部动作）。
    """
    _authorize_hub_handler(db, body.hub_issue_id, user)
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    prev = hub.status

    # 审核时可修正模块归类（覆盖 AI 判定），同步回关联 ticket + upsert_catalog 建目录。
    if body.product_line_code is not None or body.module is not None:
        from app.services.ingest.catalog_upsert import upsert_catalog

        eff_plc = (
            body.product_line_code if body.product_line_code is not None else hub.product_line_code
        )
        eff_module = body.module if body.module is not None else hub.module
        upsert_catalog(db, product_line_code=eff_plc, module=eff_module)
        hub.product_line_code = eff_plc
        hub.module = eff_module
        for tk in db.query(Ticket).filter(
            Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)
        ):
            tk.product_line_code = eff_plc
            tk.module = eff_module

    _require_module_assigned(hub)

    if hub.type == "Operation":
        hub.status = "created"
        apply_op_status(
            db,
            hub,
            to_status=OP_PROCESSING,
            handler="agent",
            reason="主管确认分类，回自动答复链",
        )
        reason = "主管确认分类"
    elif hub.type in ("Bug_fix", "Demand"):
        if peek_module_owner(db, hub.product_line_code, hub.module) is not None:
            hub.status = "created"
            reason = "确认分类，推送 Linear"
        else:
            hub.status = "pending_linear_review"
            hub.owner_user_id = default_owner_from_ticket_handler(db, hub)
            reason = "确认分类，待处理人确认后推 Linear"
    else:  # Internal_task / Complaint
        hub.status = "created"
        reason = "主管确认分类"

    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status=hub.status,
        changed_by=f"user:{user.name}",
        reason=reason,
    )
    record_ticket_action(
        db,
        hub,
        action="confirm_classification",
        changed_by=f"user:{user.name}",
        reason=reason,
    )
    db.commit()

    if hub.type in ("Bug_fix", "Demand") and hub.status == "created":
        background_tasks.add_task(push_hub_issue_to_linear, hub.id)

    # 接管主路径已改回入库/派单后立即触发（webhooks.py::_run_ksm_takeover）；
    # 此处保留兜底重试——若入库瞬间接管因故失败（网络/KSM侧异常），审核确认
    # 时再补一次，takeover_ksm_ticket 内部按 ksm_takeover_status 幂等跳过已接管。
    # 非 KSM 来源/关闭时函数内部自行判断跳过。
    _schedule_ksm_takeover(background_tasks, db, hub.id)

    logger.info(
        "supervisor_confirm_classification",
        hub_issue_id=hub.id,
        type=hub.type,
        status=hub.status,
        operator=user.name,
    )
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)


@router.post("/reclassify", response_model=ClassificationActionResponse)
def reclassify(
    body: ReclassifyBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ClassificationActionResponse:
    """改判分类（主管/管理员，或本工单处理人本人）。改判本身即视为已确认分类，按新类型分流：

    - Operation → 回炉自动答复链（op_status=processing/agent）。
    - Bug_fix/Demand → 按模块负责人是否确定分流：确定 → created + 推 Linear；
      不确定 → pending_linear_review 待处理人确认（工作台选人推送）。
    - Internal_task/Complaint → created，不推 Linear、不走答复。
    """
    # 处理中 Operation 转研发是「处理人跟客户沟通后判断是需求/Bug」的场景，
    # 处理人本人即可操作（对齐 PATCH /attributes 的授权口径），无需主管介入。
    _authorize_hub_handler(db, body.hub_issue_id, user)
    hub = _get_reclassifiable_hub(db, body.hub_issue_id)
    old_type = hub.type
    old_status = hub.status
    # 中文类型（写进用户可见的 reason，避免英文枚举 Bug_fix 直显）
    old_zh = HUB_TYPE_ZH.get(old_type, old_type)
    new_zh = HUB_TYPE_ZH.get(body.new_type, body.new_type)
    hub.type = body.new_type
    # Operation → 研发类：清 Operation 专属字段。处理中(processing)本无 reply_content，
    # 但显式清 op_status/op_handler 等，避免研发类残留运营态（违反字段契约），
    # 且满足 ck_hub_issues_operation_fields（研发类要求 reply_content/authored_by 为 NULL）。
    if old_type == "Operation" and body.new_type in ("Bug_fix", "Demand"):
        hub.reply_content = None
        hub.reply_authored_by = None
        hub.reply_updated_at = None
        hub.op_status = None
        hub.op_handler = None
        hub.op_status_changed_at = None
        hub.op_handler_user_id = None
    # 同步改判所有关联 ticket 的 predicted_type + 写修正审计（human_confirmed）。
    # 一个 hub 可能挂多条 ticket（dedup 合并），全部更新——否则工单列表「AI 分类」
    # 列（读 ticket.predicted_type）仍显示旧类型，与 hub.type 不一致。
    linked = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    for tk in linked:
        tk.predicted_type = body.new_type
        db.add(
            AgentDecision(
                decision_type="classify_type",
                subject_type="ticket",
                subject_id=tk.id,
                proposal={
                    "predicted_type": body.new_type,
                    "reason": body.reason or f"改判 {old_zh}→{new_zh}",
                    "skill": "manual",
                    "human_confirmed": True,
                    "changed_by": f"user:{user.name}",
                },
            )
        )
    _require_module_assigned(hub)

    if body.new_type == "Operation":
        hub.status = "created"
        apply_op_status(
            db,
            hub,
            to_status=OP_PROCESSING,
            handler="agent",
            reason=f"改判 {old_zh}→运营，回炉答复链",
        )
        # 处理人已在入库时按来源+规则分派好（ticket.handler_user_id），改判类型
        # 不重新分派，直接沿用（同 confirm-classification/PATCH attributes 口径）。
        db.flush()
        handler_uid = default_owner_from_ticket_handler(db, hub)
        if handler_uid is not None:
            hub.op_handler_user_id = handler_uid
    elif body.new_type in ("Bug_fix", "Demand"):
        # 按模块负责人是否确定分流：确定 → created 直推 Linear；不确定 →
        # pending_linear_review 待处理人确认（工作台选人推送）。统一口径，不再
        # 区分「处理中 Operation 转研发」与「pending_review 原路径」。
        if peek_module_owner(db, hub.product_line_code, hub.module) is not None:
            hub.status = "created"
        else:
            hub.status = "pending_linear_review"  # 待处理人确认后推 Linear
            hub.owner_user_id = default_owner_from_ticket_handler(db, hub)
    else:  # Internal_task / Complaint：不推 Linear、不走答复
        hub.status = "created"
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=old_status,
        to_status=hub.status,
        changed_by=f"user:{user.name}",
        reason=f"改判 {old_zh}→{new_zh}: {body.reason}",
    )
    record_ticket_action(
        db,
        hub,
        action="reclassify",
        changed_by=f"user:{user.name}",
        reason=f"改判为 {new_zh}",
    )
    db.commit()

    # 直推 Linear：处理中 Operation 转研发（用户决策直推），或闸门③关的 pending_review 路径。
    # status='created' 即已定为直推分支，据此触发（与上方 status 分流一致）。
    if body.new_type in ("Bug_fix", "Demand") and hub.status == "created":
        background_tasks.add_task(push_hub_issue_to_linear, hub.id)

    # 改判本身即视为已确认分类，同 confirm-classification 一样触发接管。
    _schedule_ksm_takeover(background_tasks, db, hub.id)

    logger.info(
        "supervisor_reclassify",
        hub_issue_id=hub.id,
        old_type=old_type,
        new_type=body.new_type,
        operator=user.name,
    )
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)


@router.post("/dismiss-classification", response_model=ClassificationActionResponse)
def dismiss_classification(
    body: DismissClassificationBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> ClassificationActionResponse:
    """主管判误报 → status closed（不推 Linear、不走答复）。"""
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    prev = hub.status
    hub.status = "closed"
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status="closed",
        changed_by=f"user:{user.name}",
        reason=f"主管判误报关闭: {body.reason}",
    )
    record_ticket_action(
        db,
        hub,
        action="dismiss_classification",
        changed_by=f"user:{user.name}",
        reason="误报关闭",
    )
    db.commit()
    logger.info("supervisor_dismiss_classification", hub_issue_id=hub.id, operator=user.name)
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)


# ---- 闸门③：待推 Linear 队列 + confirm-linear-push -----------------------


class PendingLinearReviewItem(BaseModel):
    hub_issue_id: int
    short_code: str
    title: str
    type: str
    product_line_code: str | None
    module: str | None
    default_assignee_user_id: int | None
    default_assignee_name: str | None
    default_assignee_in_linear: bool


class PendingLinearReviewResponse(BaseModel):
    items: list[PendingLinearReviewItem]


@router.get("/pending-linear-review", response_model=PendingLinearReviewResponse)
def list_pending_linear_review(
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> PendingLinearReviewResponse:
    """闸门③：status=='pending_linear_review' 的研发类 hub 队列，每条附默认模块
    负责人（peek_module_owner，仅预览不消耗轮询名额）及其是否在 Linear 工作区（linear_user_id 非空）。

    行级可见性：主管/admin 看全部；处理人只看处理人=自己的（_handler_scope）。
    """
    q = db.query(HubIssue).filter(
        HubIssue.deleted_at.is_(None),
        HubIssue.status == "pending_linear_review",
        HubIssue.type.in_(["Bug_fix", "Demand"]),
    )
    scope = _handler_scope(db, user)
    if scope is not None:
        q = q.filter(scope)
    hubs = q.order_by(HubIssue.id.desc()).limit(min(limit, 100)).all()
    items: list[PendingLinearReviewItem] = []
    for h in hubs:
        owner = peek_module_owner(db, h.product_line_code, h.module)
        items.append(
            PendingLinearReviewItem(
                hub_issue_id=h.id,
                short_code=h.short_code,
                title=h.title,
                type=h.type,
                product_line_code=h.product_line_code,
                module=h.module,
                default_assignee_user_id=owner.id if owner else None,
                default_assignee_name=owner.name if owner else None,
                default_assignee_in_linear=bool(owner and owner.linear_user_id),
            )
        )
    return PendingLinearReviewResponse(items=items)


class ConfirmLinearPushBody(BaseModel):
    hub_issue_id: int
    assignee_user_id: int | None = None


@router.post("/confirm-linear-push", response_model=ClassificationActionResponse)
def confirm_linear_push(
    body: ConfirmLinearPushBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ClassificationActionResponse:
    """主管/处理人确认推 Linear：手选 assignee 或回落模块负责人 → created + 推送。
    权限放宽到处理人本人（_authorize_hub_handler）。"""
    _authorize_hub_handler(db, body.hub_issue_id, user)
    hub = db.get(HubIssue, body.hub_issue_id)
    if hub is None or hub.deleted_at is not None or hub.status != "pending_linear_review":
        raise HTTPException(
            status_code=409,
            detail=f"hub_issue {body.hub_issue_id} 非 pending_linear_review，不可确认推送",
        )
    assignee_user_id = body.assignee_user_id
    if assignee_user_id is None:
        owner = consume_module_owner(db, hub.product_line_code, hub.module)
        assignee_user_id = owner.id if owner else None

    prev = hub.status
    hub.status = "created"
    hub.owner_user_id = assignee_user_id
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status="created",
        changed_by=f"user:{user.name}",
        reason="确认推送 Linear",
    )
    record_ticket_action(
        db,
        hub,
        action="confirm_linear_push",
        changed_by=f"user:{user.name}",
        reason="确认推 Linear",
    )
    db.commit()

    background_tasks.add_task(
        push_hub_issue_to_linear, hub.id, assignee_override_user_id=assignee_user_id
    )

    logger.info(
        "supervisor_confirm_linear_push",
        hub_issue_id=hub.id,
        assignee_user_id=assignee_user_id,
        operator=user.name,
    )
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)
