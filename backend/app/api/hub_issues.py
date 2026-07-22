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

from app.api.deps.auth import AuthedUser, require_knowledge_op, require_supervisor, require_user
from app.core.logging import get_logger
from app.db import get_session
from app.models import HubIssue
from app.repositories.ticket import HubIssueRepository, TicketRepository
from app.services.agents.operation_answer import auto_answer_operation
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.cascade.supply_sync import SupplySyncError, request_supply
from app.services.hub_issues.op_status import OP_PROCESSING

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
    # 研发协同（2026-07 重构）：催办 / 发版通知 / 回访 / 自查 / 停留
    urge_count: int = 0
    last_urged_at: datetime | None = None
    release_notified_at: datetime | None = None
    fix_version: str | None = None
    feedback_status: str | None = None
    feedback_note: str | None = None
    self_found: bool = False
    status_changed_at: datetime | None = None
    # Operation 状态机（op_status 专属层，仅 Operation 非空；研发类恒 NULL）
    op_status: str | None = None
    op_handler: str | None = None
    reject_count: int = 0
    op_status_changed_at: datetime | None = None

    model_config = {"from_attributes": True}


class LinkedTicket(BaseModel):
    id: int
    short_code: str
    source_code: str | None
    source_ticket_id: str | None
    status: str

    model_config = {"from_attributes": True}


class SubIssueItem(BaseModel):
    """owner-split 子 issue（ADR-0016 P4）— 详情页里程碑列表行。"""

    id: int
    linear_identifier: str
    title: str
    assignee_user_id: int | None
    status: str | None  # 镜像 Linear 列名
    state_type: str | None
    released_at: datetime | None
    notified_at: datetime | None

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
    # owner-split 子 issue 里程碑（ADR-0016 P4）
    sub_issues: list[SubIssueItem] = []


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
    from app.models import HubIssueLinearIssue

    subs = (
        db.query(HubIssueLinearIssue)
        .filter(HubIssueLinearIssue.hub_issue_id == hub_issue_id)
        .order_by(HubIssueLinearIssue.id)
        .all()
    )
    detail.sub_issues = [SubIssueItem.model_validate(s) for s in subs]
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


# ---- 人工重答（Task 8，人工介入中主管改完 KB/skill 后同步重答一次）----------


class ReAnswerResponse(BaseModel):
    hub_issue_id: int
    op_status: str
    answered: bool


@router.post("/{hub_issue_id}/re-answer", response_model=ReAnswerResponse)
def re_answer_endpoint(
    hub_issue_id: int,
    user: AuthedUser = Depends(require_knowledge_op),
    db: Session = Depends(get_session),
) -> ReAnswerResponse:
    """主管/知识运营改完 KB 或 skill 后手动重答一次（同步，非 drain 异步）。

    前置：hub 存在 + type=Operation + op_status=processing 且 op_handler!=
    'agent'（人工介入中）。非人工介入中一律 409（含刚毕业未处理过、已答复、
    补料中等——这些场景走各自专属流程，不该被重答抢跑）。
    """
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_issue_id} not found")
    if hub.type != "Operation":
        raise HTTPException(
            status_code=409,
            detail=f"hub_issue {hub.short_code} is type={hub.type!r} — re-answer is Operation-only",
        )
    if hub.op_status != OP_PROCESSING or hub.op_handler == "agent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"hub_issue {hub.short_code} op_status={hub.op_status!r} "
                f"op_handler={hub.op_handler!r} — re-answer requires 人工介入中 "
                f"(op_status=processing and op_handler!='agent')"
            ),
        )

    answered = auto_answer_operation(db, hub_issue_id, force=True)
    db.refresh(hub)
    logger.info(
        "hub_issue_re_answered",
        hub_issue_id=hub_issue_id,
        answered=answered,
        op_status=hub.op_status,
        operator_user_id=user.user_id,
    )
    return ReAnswerResponse(hub_issue_id=hub.id, op_status=hub.op_status or "", answered=answered)


# ---- 研发协同（2026-07 后台重构 批次5）--------------------------------------


class UrgeResponse(BaseModel):
    hub_issue_id: int
    urge_count: int
    linear_identifier: str


@router.post("/{hub_issue_id}/urge", response_model=UrgeResponse)
def urge_endpoint(
    hub_issue_id: int,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> UrgeResponse:
    """催办：向 Linear issue 发评论并计数（24h 频率限制）。"""
    from app.services.hub_issues import devcollab as dc

    try:
        r = dc.urge_hub_issue(db, hub_issue_id, urged_by=f"user:{user.name}")
    except dc.DevCollabError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return UrgeResponse(
        hub_issue_id=r.hub_issue_id,
        urge_count=r.urge_count,
        linear_identifier=r.linear_identifier,
    )


class NotifyReleaseBody(BaseModel):
    fix_version: str = Field(..., min_length=1, max_length=64)
    note: str = Field(..., min_length=1, max_length=4000)


class NotifyReleaseResponse(BaseModel):
    hub_issue_id: int
    channel_count: int  # 入队 outbox 的客户渠道数


@router.post("/{hub_issue_id}/notify-release", response_model=NotifyReleaseResponse)
def notify_release_endpoint(
    hub_issue_id: int,
    body: NotifyReleaseBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> NotifyReleaseResponse:
    """发版通知：文案入 outbox（每个有源关联工单一行），回访状态置 pending。"""
    from app.services.hub_issues import devcollab as dc

    try:
        r = dc.notify_release(
            db,
            hub_issue_id,
            fix_version=body.fix_version,
            note=body.note,
            notified_by=f"user:{user.name}",
        )
    except dc.DevCollabError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return NotifyReleaseResponse(hub_issue_id=r.hub_issue_id, channel_count=len(r.outbox_ids))


class SelfBugBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    product_line_code: str | None = None
    module: str | None = None
    impact_versions: str | None = Field(default=None, max_length=128)
    fix_version: str | None = Field(default=None, max_length=64)
    released: bool = True


class SelfBugResponse(BaseModel):
    hub_issue_id: int
    short_code: str


@router.post("/self-bug", response_model=SelfBugResponse)
def self_bug_endpoint(
    body: SelfBugBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> SelfBugResponse:
    """登记自修复 bug：无客户来源的 standalone Bug_fix hub 工单（自查徽标）。"""
    from app.services.hub_issues import devcollab as dc

    try:
        r = dc.register_self_bug(
            db,
            title=body.title,
            product_line_code=body.product_line_code,
            module=body.module,
            impact_versions=body.impact_versions,
            fix_version=body.fix_version,
            released=body.released,
            registered_by=f"user:{user.name}",
        )
    except dc.DevCollabError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return SelfBugResponse(hub_issue_id=r.hub_issue_id, short_code=r.short_code)


class OwnerSplitSubTask(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    assignee_user_id: int | None = None


class OwnerSplitBody(BaseModel):
    subtasks: list[OwnerSplitSubTask] = Field(..., min_length=2, max_length=20)


class OwnerSplitSubIssueOut(BaseModel):
    id: int
    linear_identifier: str
    title: str
    assignee_user_id: int | None


class OwnerSplitResponse(BaseModel):
    hub_issue_id: int
    sub_issues: list[OwnerSplitSubIssueOut]


@router.post("/{hub_issue_id}/owner-split", response_model=OwnerSplitResponse)
def owner_split_endpoint(
    hub_issue_id: int,
    body: OwnerSplitBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> OwnerSplitResponse:
    """按责任人拆分（ADR-0016 P4 v1 手动）：N 个子任务 → N 个 Linear 子 issue
    （parentId 挂主 issue）+ 跟踪行。每子 issue Done 由轮询自动发 x/n 进度通知。"""
    from app.services.hub_issues import owner_split as os_svc

    try:
        r = os_svc.execute_owner_split(
            db,
            hub_issue_id,
            subtasks=[
                os_svc.SubTaskIn(title=s.title, assignee_user_id=s.assignee_user_id)
                for s in body.subtasks
            ],
            executed_by=f"user:{user.name}",
        )
    except os_svc.OwnerSplitError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info(
        "hub_issue_owner_split",
        hub_issue_id=hub_issue_id,
        n=len(r.sub_issues),
        operator_user_id=user.user_id,
    )
    return OwnerSplitResponse(
        hub_issue_id=r.hub_issue_id,
        sub_issues=[
            OwnerSplitSubIssueOut(
                id=s.id,
                linear_identifier=s.linear_identifier,
                title=s.title,
                assignee_user_id=s.assignee_user_id,
            )
            for s in r.sub_issues
        ],
    )


class FeedbackBody(BaseModel):
    status: str = Field(..., pattern="^(resolved|stillbad)$")
    note: str = Field(default="", max_length=2000)


class FeedbackResponse(BaseModel):
    hub_issue_id: int
    feedback_status: str


@router.post("/{hub_issue_id}/feedback", response_model=FeedbackResponse)
def feedback_endpoint(
    hub_issue_id: int,
    body: FeedbackBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> FeedbackResponse:
    """记录发版后客户回访结果（resolved 闭环 / stillbad 待升级）。"""
    from app.services.hub_issues import devcollab as dc

    try:
        r = dc.record_feedback(
            db, hub_issue_id, status=body.status, note=body.note, recorded_by=f"user:{user.name}"
        )
    except dc.DevCollabError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return FeedbackResponse(hub_issue_id=r.hub_issue_id, feedback_status=r.feedback_status)
