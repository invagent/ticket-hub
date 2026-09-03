"""GET /api/hub-issues — list / detail；POST reply（D4 第②段）.

  GET  /api/hub-issues?type=&status=&assigned_user_id=&product=&module=&page=&page_size=
  GET  /api/hub-issues/{hub_issue_id}            includes linked tickets (summary)
  POST /api/hub-issues/{hub_issue_id}/reply      author Operation reply (supervisor)

All authenticated users can read; replies require supervisor.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_supervisor, require_user
from app.core.logging import get_logger
from app.db import get_session
from app.models import AgentDecision, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import HubIssueRepository, TicketRepository
from app.services import knowledge_feedback as kf
from app.services.agents.operation_answer import auto_answer_operation
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.cascade.supply_sync import SupplySyncError, request_supply
from app.services.dispatch import dispatch_handler
from app.services.hub_issues.module_owner import peek_module_owner
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_CLOSED,
    OP_EXCEPTION,
    OP_PROCESSING,
    apply_op_status,
    default_owner_from_ticket_handler,
    record_ticket_action,
    set_hub_tickets_handler,
)
from app.services.ingest.catalog_upsert import upsert_catalog

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
    # 责任人：默认=处理人，推 Linear 后=推送时确定的模块负责人（module_owner.py）
    owner_user_id: int | None = None
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


class TransferAttempt(BaseModel):
    """AI 转人工时的已尝试问答（只读展示，见 last_transfer_attempt）。"""

    question: str
    answer: str


class HubIssueDetail(HubIssueSummary):
    canonical_body: str | None
    # Operation-only（其余 Operation 字段已在 Summary）
    reply_content: str | None
    reply_authored_by: str | None
    reply_is_draft: bool = False  # True=AI 草稿（处理说明待人工审核/发送），前端据此提示
    op_handler_user_id: int | None = None  # 运营处理人 uid，前端据此判「处理人本人」可编辑
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
    # 补料清单：AI 判定需补料时生成的「需补充资料」文本（仅 op_status=supplementing 回填）
    supply_note: str | None = None
    # AI 转人工时已尝试的问答（仅 op_status=processing 且最新 auto_reply 判 transfer 回填）；
    # 只读展示用，不进 reply_content/处理说明草稿——避免被误当正式答复发出。
    last_transfer_attempt: TransferAttempt | None = None


class HubIssueListResponse(BaseModel):
    items: list[HubIssueSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


# 时间区间按北京时区（+08:00）解释 date 边界，再转 UTC（hub 时间字段是 tz-aware UTC）。
def _day_bounds(from_d: date | None, to_d: date | None) -> tuple[datetime | None, datetime | None]:
    """[from 当天 00:00, to+1天 00:00) 半开区间，按北京时区解释后转 UTC。"""
    bj = timezone(timedelta(hours=8))
    from_dt = datetime.combine(from_d, time.min, tzinfo=bj).astimezone(UTC) if from_d else None
    to_dt = (
        datetime.combine(to_d + timedelta(days=1), time.min, tzinfo=bj).astimezone(UTC)
        if to_d
        else None
    )
    return from_dt, to_dt


@router.get("", response_model=HubIssueListResponse)
def list_hub_issues(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    type: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    assigned_user_id: int | None = Query(None),
    product: str | None = Query(None),
    module: str | None = Query(None),
    search: str | None = Query(None),
    op_status: str | None = Query(None),  # 工单状态=运营处理状态（仅 Operation 有值）
    dev_stage: str | None = Query(None),  # 研发状态：精确匹配实际 linear_status
    created_from: date | None = Query(None),
    created_to: date | None = Query(None),
    closed_from: date | None = Query(None),
    closed_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> HubIssueListResponse:
    cf, ct = _day_bounds(created_from, created_to)
    clf, clt = _day_bounds(closed_from, closed_to)
    p = HubIssueRepository(db).list_paginated(
        type_=type,
        status=status,
        assigned_user_id=assigned_user_id,
        product=product,
        module=module,
        search=search,
        op_status=op_status,
        dev_stage=dev_stage,
        created_from=cf,
        created_to=ct,
        closed_from=clf,
        closed_to=clt,
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


class FilterCountsResponse(BaseModel):
    op_status: dict[str, int]  # {processing, answered, ..., all} 运营处理状态各档
    dev_stage: dict[str, int]  # {实际 linear_status 值: 数}
    type: dict[str, int]  # {Operation, Bug_fix, Demand, Internal_task, all} 任务类型各档


@router.get("/filter-counts", response_model=FilterCountsResponse)
def hub_issue_filter_counts(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    type: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    assigned_user_id: int | None = Query(None),
    product: str | None = Query(None),
    module: str | None = Query(None),
    search: str | None = Query(None),
    op_status: str | None = Query(None),
    dev_stage: str | None = Query(None),
    created_from: date | None = Query(None),
    created_to: date | None = Query(None),
    closed_from: date | None = Query(None),
    closed_to: date | None = Query(None),
) -> FilterCountsResponse:
    """各筛选维度的全量分档计数（跨页真实值，供 chip 上的 (数量) 显示）。"""
    cf, ct = _day_bounds(created_from, created_to)
    clf, clt = _day_bounds(closed_from, closed_to)
    counts = HubIssueRepository(db).filter_counts(
        type_=type,
        status=status,
        assigned_user_id=assigned_user_id,
        product=product,
        module=module,
        search=search,
        op_status=op_status,
        dev_stage=dev_stage,
        created_from=cf,
        created_to=ct,
        closed_from=clf,
        closed_to=clt,
    )
    return FilterCountsResponse(**counts)


class ProductOptionsResponse(BaseModel):
    products: list[str]  # 数据里实际存在的 product_line_code（按数量降序）


@router.get("/product-options", response_model=ProductOptionsResponse)
def hub_issue_product_options(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProductOptionsResponse:
    """产品分类筛选下拉的实际可选值（数据驱动，不写死清单）。"""
    return ProductOptionsResponse(products=HubIssueRepository(db).distinct_product_lines())


class DevStatusOptionsResponse(BaseModel):
    statuses: list[str]  # 数据里实际存在的 linear_status（按数量降序）


@router.get("/dev-status-options", response_model=DevStatusOptionsResponse)
def hub_issue_dev_status_options(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> DevStatusOptionsResponse:
    """研发状态筛选下拉的实际可选值（数据驱动的 linear_status；工单推 Linear 后有值）。"""
    return DevStatusOptionsResponse(statuses=HubIssueRepository(db).distinct_linear_statuses())


class CatalogModuleOut(BaseModel):
    code: str
    name: str


@router.get("/catalog/modules", response_model=list[CatalogModuleOut])
def list_catalog_modules(
    product_line_code: str | None = Query(None),
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> list[CatalogModuleOut]:
    """处理人可读的模块下拉（require_user）。按 product_line_code 过滤，仅 active。

    工单参数编辑区的模块下拉数据源；admin_catalog 的同类端点是 require_admin，
    处理人够不到，故在此另开一个只读端点。Module 无独立 code 列，用 name 兼作 code。
    """
    from app.models import Module

    q = db.query(Module).filter(Module.is_active.is_(True))
    if product_line_code:
        q = q.filter(Module.product_line_code == product_line_code)
    rows = q.order_by(Module.name).all()
    return [CatalogModuleOut(code=m.name, name=m.name) for m in rows]


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
    # 补料态：回填 AI 生成的「需补充资料」清单（取最新 auto_reply decision 的 supply_note）
    if hub.op_status == "supplementing":
        dec = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.subject_type == "hub_issue",
                AgentDecision.subject_id == hub_issue_id,
                AgentDecision.decision_type == "auto_reply",
            )
            .order_by(AgentDecision.id.desc())
            .first()
        )
        if dec and dec.proposal:
            note = dec.proposal.get("supply_note")
            if note:
                detail.supply_note = str(note)
    # processing 态：回填最新一条 AI 转人工审计（branch=transfer）的已尝试问答，
    # 供处理说明区只读提示——人工判断 AI 是否已经答过、答了什么。
    if hub.op_status == "processing":
        dec = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.subject_type == "hub_issue",
                AgentDecision.subject_id == hub_issue_id,
                AgentDecision.decision_type == "auto_reply",
            )
            .order_by(AgentDecision.id.desc())
            .first()
        )
        if dec and dec.proposal and dec.proposal.get("branch") == "transfer":
            question = dec.proposal.get("question")
            answer = dec.proposal.get("answer")
            if question and answer:
                detail.last_transfer_attempt = TransferAttempt(
                    question=str(question), answer=str(answer)
                )
    return detail


# ---- Operation reply (决策 15, D4 第②段) ------------------------------------


class AuthorReplyBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class AuthorReplyResponse(BaseModel):
    hub_issue_id: int
    version: int
    cascaded_ticket_count: int
    outbox_count: int  # 入队待回写源系统的条数（D5 sender 消费）


def _authorize_hub_handler(
    db: Session,
    hub_issue_id: int,
    user: AuthedUser,
    *,
    base_roles: tuple[str, ...] = ("supervisor", "admin"),
) -> None:
    """处理动作授权：base_roles 内的角色直接放行；否则要求当前用户是该 hub 的
    Operation 处理人（op_handler_user_id），即"处理人可操作自己手上的工单"。都不满足 → 403。

    hub 不存在时不在此报错（放行给端点内的 404 逻辑处理）。
    """
    if user.role in base_roles:
        return
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None:
        return  # 交给端点内 404
    if hub.op_handler_user_id == user.user_id:
        return
    # hub 层没落运营处理人（历史单 / 未走分派的单），但处理人身份落在 ticket 层
    # （ticket.handler_user_id，工单详情页据此判可见性/持有）。授权口径与可见性
    # 口径对齐：该用户是本 hub 任一关联工单的处理人即放行。
    is_ticket_handler = (
        db.query(Ticket.id)
        .filter(Ticket.hub_issue_id == hub_issue_id, Ticket.handler_user_id == user.user_id)
        .first()
        is not None
    )
    if is_ticket_handler:
        return
    raise HTTPException(
        status_code=403,
        detail="需要主管/管理员权限，或本工单的处理人才能操作",
    )


@router.post("/{hub_issue_id}/reply", response_model=AuthorReplyResponse)
def author_reply_endpoint(
    hub_issue_id: int,
    body: AuthorReplyBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> AuthorReplyResponse:
    _authorize_hub_handler(db, hub_issue_id, user)
    """Author/replace the Operation reply. Cascades to linked tickets'
    cached_reply and enqueues sync_outbox rows for source write-back."""
    hub_before = db.get(HubIssue, hub_issue_id)
    if (
        hub_before is not None
        and hub_before.type == "Operation"
        and hub_before.op_status == OP_CLOSED
    ):
        raise HTTPException(
            status_code=409,
            detail=f"hub_issue {hub_before.short_code} 已关单（op_status=closed），不允许再写答复",
        )

    try:
        result = author_reply(
            db, hub_issue_id, content=body.content, authored_by=f"user:{user.name}"
        )
    except ReplySyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 人工答复推进 op_status → answered（补齐与 agent 答复的对称性）。
    # author_reply 已 commit；apply_op_status 不 commit，故此处单独 commit。
    hub = db.get(HubIssue, hub_issue_id)
    if hub is not None and hub.type == "Operation":
        apply_op_status(
            db, hub, to_status=OP_ANSWERED, handler=f"user:{user.name}", reason="主管人工答复"
        )
        # 答复留痕：给关联工单写时间轴节点（详情页左侧「处理节点」）
        record_ticket_action(
            db, hub, action="reply", changed_by=f"user:{user.name}", reason="主管答复客户"
        )
        # 答复者成为处理人（hub 下所有关联工单同步）
        set_hub_tickets_handler(db, hub, user.user_id)
        db.commit()

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
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> RequestSupplyResponse:
    """Ask the customer for more info (补料). Enqueues a supply sync_outbox row
    per linked sourced ticket; the KSM sender drains them into supplyKsmOrder."""
    _authorize_hub_handler(db, hub_issue_id, user)
    hub_pre = db.get(HubIssue, hub_issue_id)
    if hub_pre is not None and hub_pre.type == "Operation" and hub_pre.op_status == OP_CLOSED:
        raise HTTPException(
            status_code=409,
            detail=f"hub_issue {hub_pre.short_code} 已关单（op_status=closed），不允许再补料",
        )
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


# ---- 标记诊断：运营单 AI 自动答复有问题 → 送反思诊断 -----------------------------


class FlagDiagnosisBody(BaseModel):
    ticket_id: int
    note: str | None = Field(default=None, max_length=2000)  # 内部复核意见，选填


class FlagDiagnosisResponse(BaseModel):
    ticket_id: int
    hub_issue_id: int
    flagged_at: datetime


@router.post("/{hub_issue_id}/flag-diagnosis", response_model=FlagDiagnosisResponse)
def flag_diagnosis_endpoint(
    hub_issue_id: int,
    body: FlagDiagnosisBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> FlagDiagnosisResponse:
    """处理人发现运营工单的 AI 自动答复有问题 → 送进反思诊断工作台（复用 ai_cs
    escalation 的读写路径，见 knowledge_feedback.service.flag_for_diagnosis）。

    权限：处理人本人或主管/admin（_authorize_hub_handler，同 reply/request-supply）。
    三个状态条件必须同时成立才允许标记：type=Operation、op_status=answered（非
    closed/processing/reviewing/exception/supplementing）、reply_authored_by ==
    'agent:ai_cs'（区分「AI 直发」vs 人工发/编辑过的答复）。
    """
    _authorize_hub_handler(db, hub_issue_id, user)
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="hub_issue not found")
    if hub.type != "Operation":
        raise HTTPException(status_code=409, detail="仅运营（Operation）工单支持标记诊断")
    if hub.op_status != OP_ANSWERED:
        raise HTTPException(
            status_code=409,
            detail=f"仅「AI 已答复未关闭」的工单可标记诊断，当前状态：{hub.op_status}",
        )
    if hub.reply_authored_by != "agent:ai_cs":
        raise HTTPException(
            status_code=409, detail="当前答复非 AI 自动答复（人工回复/编辑过），无需诊断"
        )
    ticket = db.get(Ticket, body.ticket_id)
    if ticket is None or ticket.deleted_at is not None or ticket.hub_issue_id != hub_issue_id:
        raise HTTPException(status_code=404, detail="ticket not found or not linked to this hub")

    dec = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.decision_type == "auto_reply",
            AgentDecision.subject_type == "hub_issue",
            AgentDecision.subject_id == hub.id,
        )
        .order_by(AgentDecision.id.desc())
        .first()
    )
    if dec is None:
        raise HTTPException(status_code=409, detail="找不到 AI 自动答复记录，无法标记诊断")
    prop = dec.proposal or {}

    try:
        kf.flag_for_diagnosis(
            db,
            body.ticket_id,
            question=str(prop.get("question") or ""),
            answer=str(prop.get("answer") or ""),
            cited_knowledge=prop.get("cited_knowledge") or [],
            skills_used=prop.get("skills_used") or [],
            note=body.note or "",
            operator=f"user:{user.name}",
        )
    except kf.AlreadyDiagnosedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    logger.info(
        "hub_issue_flag_diagnosis",
        hub_issue_id=hub_issue_id,
        ticket_id=body.ticket_id,
        operator_user_id=user.user_id,
    )
    assert ticket.diagnosis_flagged_at is not None
    return FlagDiagnosisResponse(
        ticket_id=body.ticket_id,
        hub_issue_id=hub_issue_id,
        flagged_at=ticket.diagnosis_flagged_at,
    )


# ---- 工单参数编辑（类型/产品线/模块，只改数据不联动）----------------------------


class UpdateAttributesBody(BaseModel):
    type: str | None = Field(None, pattern="^(Operation|Bug_fix|Demand|Internal_task|Complaint)$")
    product_line_code: str | None = Field(None, max_length=64)
    module: str | None = Field(None, max_length=128)


class UpdateAttributesResponse(BaseModel):
    hub_issue_id: int
    type: str
    product_line_code: str | None
    module: str | None
    updated_ticket_count: int


@router.patch("/{hub_issue_id}/attributes", response_model=UpdateAttributesResponse)
def update_hub_attributes(
    hub_issue_id: int,
    body: UpdateAttributesBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> UpdateAttributesResponse:
    """改 type/product_line_code/module。改 type 时按新类型规整下游状态（status/
    op_status/dispatch），口径与 reclassify 一致，避免留下 pending_linear_review
    等旧类型专属状态卡死的孤儿态；但不主动推 Linear（研发类改判后仍需人工
    repush-linear 或走 confirm-linear-push，保留主管手动把关）。
    处理人本人/主管/管理员可改；已关闭 409。"""
    _authorize_hub_handler(db, hub_issue_id, user)
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="hub_issue not found")
    if hub.status == "closed" or (hub.type == "Operation" and hub.op_status == OP_CLOSED):
        raise HTTPException(
            status_code=409, detail=f"hub_issue {hub.short_code} 已关闭，不可修改参数"
        )

    changes: list[str] = []
    updated_tickets = 0
    if body.type is not None and body.type != hub.type:
        old = hub.type
        hub.type = body.type
        # Operation → 研发类/内部任务：清 Operation 专属字段，否则违反
        # ck_hub_issues_operation_fields（非 Operation 要求 reply_content/authored_by 为 NULL）。
        # 与 reclassify 的清空口径一致。
        if old == "Operation" and body.type in ("Bug_fix", "Demand", "Internal_task"):
            hub.reply_content = None
            hub.reply_authored_by = None
            hub.reply_updated_at = None
            hub.op_status = None
            hub.op_handler = None
            hub.op_status_changed_at = None
            hub.op_handler_user_id = None
        linked = (
            db.query(Ticket)
            .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
            .all()
        )
        for tk in linked:
            tk.predicted_type = body.type
            db.add(
                AgentDecision(
                    decision_type="classify_type",
                    subject_type="ticket",
                    subject_id=tk.id,
                    proposal={
                        "predicted_type": body.type,
                        "reason": f"手动修改 {old}→{body.type}",
                        "skill": "manual",
                        "human_confirmed": True,
                        "changed_by": f"user:{user.name}",
                    },
                )
            )
        updated_tickets = len(linked)
        changes.append(f"类型 {old}→{body.type}")

        # status 规整：仅对「已确认过分类」的 hub 生效（pending_review 待确认
        # 分类的 hub 交给 confirm-classification/graduate 决定初次分流，这里不
        # 抢它的活，否则光改类型下拉框还没点确认就被推进 created/pending_linear_review）。
        if hub.status != "pending_review":
            # 旧类型专属的 status（pending_linear_review/pending 都是研发类
            # Linear 推送流程专属）留在非研发类上会变成孤儿态——不进任何队列、
            # 也不会被任何自动链捡起（TKT-006584 教训：Bug_fix→Operation 后卡在
            # pending_linear_review，既不进运营答复链也从待推 Linear 队列消失）。
            if hub.status in ("pending_linear_review", "pending") and body.type not in (
                "Bug_fix",
                "Demand",
            ):
                hub.status = "created"

            if body.type == "Operation":
                # 进入运营类：回炉自动答复链（op_status=processing/agent）+ 按
                # 分派引擎预分配运营处理人，口径与 reclassify/confirm-classification 一致。
                hub.status = "created"
                apply_op_status(
                    db,
                    hub,
                    to_status=OP_PROCESSING,
                    handler="agent",
                    reason=f"手动修改 {old}→运营，回炉答复链",
                )
                db.flush()
                dr = dispatch_handler(db, hub)
                if dr.user_id is not None:
                    hub.op_handler_user_id = dr.user_id
                    set_hub_tickets_handler(db, hub, dr.user_id)
            elif body.type in ("Bug_fix", "Demand") and hub.status not in (
                "pending_linear_review",
                "pending",
            ):
                # 进入研发类：模块负责人不确定则停 pending_linear_review 待人工
                # 选人推送（不主动推 Linear，留给主管走 confirm-linear-push/repush-linear）。
                if peek_module_owner(db, hub.product_line_code, hub.module) is None:
                    hub.status = "pending_linear_review"
                    hub.owner_user_id = default_owner_from_ticket_handler(db, hub)
                elif hub.status != "created":
                    hub.status = "created"
    if body.product_line_code is not None and body.product_line_code != hub.product_line_code:
        changes.append(f"产品线 {hub.product_line_code}→{body.product_line_code}")
        hub.product_line_code = body.product_line_code
    if body.module is not None and body.module != hub.module:
        changes.append(f"模块 {hub.module}→{body.module}")
        hub.module = body.module

    if body.product_line_code or body.module:
        upsert_catalog(db, product_line_code=hub.product_line_code, module=hub.module)

    if changes:
        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=hub.status,
            to_status=hub.status,
            changed_by=f"user:{user.name}",
            reason="修改工单参数: " + "; ".join(changes),
        )
        record_ticket_action(
            db,
            hub,
            action="edit_attributes",
            changed_by=f"user:{user.name}",
            reason="; ".join(changes),
        )
    db.commit()
    return UpdateAttributesResponse(
        hub_issue_id=hub.id,
        type=hub.type,
        product_line_code=hub.product_line_code,
        module=hub.module,
        updated_ticket_count=updated_tickets,
    )


# ---- 人工重答（Task 8，人工介入中主管改完 KB/skill 后同步重答一次）----------


class ReAnswerResponse(BaseModel):
    hub_issue_id: int
    op_status: str
    answered: bool


@router.post("/{hub_issue_id}/re-answer", response_model=ReAnswerResponse)
def re_answer_endpoint(
    hub_issue_id: int,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ReAnswerResponse:
    """主管/知识运营改完 KB 或 skill 后手动重答一次（同步，非 drain 异步）。

    前置：hub 存在 + type=Operation + op_status ∈ (processing, exception) 且
    op_handler != 'agent'。processing 是人工介入中；exception 是 replay 系统
    故障时置的转人工态——drain 不会自动重扫 exception（系统故障不该无限自动
    重试，要人工介入），所以重答是它唯一的恢复出路，主管修完系统故障后应能
    点重答把它拉回处理流程。非以上组合一律 409（含刚毕业未处理过、已答复、
    补料中等——这些场景走各自专属流程，不该被重答抢跑）。

    权限：supervisor/admin/knowledge_op，或本工单的处理人（op_handler_user_id）。
    """
    _authorize_hub_handler(
        db, hub_issue_id, user, base_roles=("supervisor", "admin", "knowledge_op")
    )
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_issue_id} not found")
    if hub.type != "Operation":
        raise HTTPException(
            status_code=409,
            detail=f"hub_issue {hub.short_code} is type={hub.type!r} — re-answer is Operation-only",
        )
    if hub.op_status not in (OP_PROCESSING, OP_EXCEPTION) or hub.op_handler == "agent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"hub_issue {hub.short_code} op_status={hub.op_status!r} "
                f"op_handler={hub.op_handler!r} — re-answer requires 人工介入中或处理异常 "
                f"(op_status in ('processing','exception') and op_handler!='agent')"
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
