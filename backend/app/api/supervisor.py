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
  GET  /api/supervisor/ai-cs/status                — knowledge-feedback feature on/configured
  GET  /api/supervisor/ai-cs/skills                — list managed AI 客服 skills
  GET  /api/supervisor/ai-cs/skills/{name}         — skill published files + history
  POST /api/supervisor/ai-cs/skills/{name}/drafts  — create a skill revision draft
  POST /api/supervisor/ai-cs/replay                — re-answer with current/draft skill (test)
  POST /api/supervisor/ai-cs/publish               — publish a skill draft to production
  GET  /api/supervisor/tickets/{id}/escalation-context — golden triple for reflect UI

All endpoints require role IN ('supervisor', 'admin').
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsBusinessError, AiCsError
from app.api.deps.auth import AuthedUser, require_supervisor
from app.core.logging import get_logger
from app.db import get_session
from app.models import HubIssue, StatusHistory
from app.repositories.notification_log import NotificationLogRepository
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
from app.services.hub_issues.creator import (
    HubIssueCreateError,
    ensure_hub_issue_for_ticket,
)
from app.services.hub_issues.linear_push import push_hub_issue_to_linear
from app.services.ksm.notice_store import NoticeStore
from app.services.ksm.writeback import drain_ksm_outbox
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


@router.post("/repush-linear", response_model=RepushLinearResponse)
def repush_linear_endpoint(
    body: RepushLinearBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RepushLinearResponse:
    """Retry a blocked Linear push (e.g. after the assignee joined Linear and
    sync-from-linear refreshed the mapping). Synchronous — the supervisor
    wants to see the outcome immediately."""
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
    original_question: str = ""
    ai_answer: str = ""
    dissatisfaction: str = ""
    # 反哺扩展（AI 客服接口1 扩展载荷；老工单为空列表）
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    cited_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)


def _ai_cs_http_error(e: AiCsError) -> HTTPException:
    """Translate adapter exceptions to HTTP. Business (bad version / not
    managed) → 400; network/auth/unavailable → 502."""
    if isinstance(e, AiCsBusinessError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=502, detail=f"AI 客服 不可用：{e}")


@router.get("/ai-cs/status", response_model=AiCsStatusResponse)
def ai_cs_status_endpoint(
    _user: AuthedUser = Depends(require_supervisor),
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
    _user: AuthedUser = Depends(require_supervisor),
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
    _user: AuthedUser = Depends(require_supervisor),
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
    user: AuthedUser = Depends(require_supervisor),
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
    user: AuthedUser = Depends(require_supervisor),
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
    user: AuthedUser = Depends(require_supervisor),
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


@router.get("/tickets/{ticket_id}/escalation-context", response_model=EscalationContextResponse)
def ai_cs_escalation_context_endpoint(
    ticket_id: int,
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> EscalationContextResponse:
    """The golden triple (原问题/AI答复/不满) for an ai_cs escalation ticket, so
    the reflect UI can seed the comparison. is_escalation=false for non-ai_cs
    tickets (UI hides the panel)."""
    ctx = kf.load_escalation_context(db, ticket_id)
    if ctx is None:
        return EscalationContextResponse(is_escalation=False, ticket_id=ticket_id)
    return EscalationContextResponse(
        is_escalation=True,
        ticket_id=ctx.ticket_id,
        session_id=ctx.session_id,
        original_question=ctx.original_question,
        ai_answer=ctx.ai_answer,
        dissatisfaction=ctx.dissatisfaction,
        conversation=ctx.conversation,
        cited_knowledge=ctx.cited_knowledge,
        skills_used=ctx.skills_used,
    )
