"""产品内提单门户接口（ADR-0017 D4）—— 面向提单人的 C/R/U，不提供 D。

  POST /api/portal/auth/token                 租户签名换门户 JWT（aud=portal）
  GET  /api/portal/me                         当前提单人
  POST /api/portal/tickets                    创建（走 embedded ingester + 完整 AI 链）
  GET  /api/portal/tickets                    我的工单（stage 筛选、分页）
  GET  /api/portal/tickets/{id}               详情 + stage 时间轴 + 答复
  PATCH /api/portal/tickets/{id}              更新标题/正文（仅 received/pending_classify/supplementing）
  POST /api/portal/tickets/{id}/supplement    追加补充资料（补料态自动回到处理中）
  GET  /api/portal/stats                      我的各 stage 计数

行级隔离：所有查询强制 `tickets.tenant_user_id == 当前提单人`；跨用户一律 404（不泄露存在性）。
`portal_enabled=False` 时 require_portal_user 直接 404。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps.portal_auth import (
    PortalUser,
    issue_portal_jwt,
    require_portal_user,
    verify_signature,
)
from app.config import get_settings
from app.core.logging import get_logger
from app.core.trace import get_trace_id
from app.db import get_session
from app.models import HubIssue, Tenant, TenantUser, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.op_status import OP_PROCESSING, OP_SUPPLEMENTING, apply_op_status
from app.services.ingest.portal_ingester import PortalIngester
from app.services.state.stage import (
    STAGE_TONE,
    TERMINAL_STAGES,
    TICKET_STAGES,
    stage_label,
)

router = APIRouter()
logger = get_logger(__name__)

# 提单人可改标题/正文的阶段：还没进入处理 / 等人确认分类 / 等提单人补料
EDITABLE_STAGES: frozenset[str] = frozenset({"received", "pending_classify", "supplementing"})


# ---- auth -------------------------------------------------------------------


class TokenRequest(BaseModel):
    tenant_code: str = Field(..., min_length=1, max_length=64)
    external_uid: str = Field(..., min_length=1, max_length=128)
    ts: int
    sign: str = Field(..., min_length=32, max_length=128)
    name: str | None = Field(None, max_length=128)
    mobile: str | None = Field(None, max_length=32)
    email: str | None = Field(None, max_length=255)


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_user_id: int
    tenant_name: str


def _portal_guard() -> None:
    if not get_settings().portal_enabled:
        raise HTTPException(status_code=404, detail="portal disabled")


@router.post("/auth/token", response_model=TokenResponse)
def issue_token(body: TokenRequest, db: Session = Depends(get_session)) -> TokenResponse:
    _portal_guard()
    settings = get_settings()
    tenant = db.execute(select(Tenant).where(Tenant.code == body.tenant_code)).scalar_one_or_none()
    # 租户不存在 / 停用 / 签名错 统一 401，不区分（防枚举）
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=401, detail="invalid tenant or signature")
    if not verify_signature(
        tenant.hmac_secret,
        body.tenant_code,
        body.external_uid,
        body.ts,
        body.sign,
        skew_seconds=settings.portal_sign_skew_seconds,
    ):
        raise HTTPException(status_code=401, detail="invalid tenant or signature")

    tu = db.execute(
        select(TenantUser).where(
            TenantUser.tenant_id == tenant.id, TenantUser.external_uid == body.external_uid
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if tu is None:
        tu = TenantUser(
            tenant_id=tenant.id,
            external_uid=body.external_uid,
            name=body.name,
            mobile=body.mobile,
            email=body.email,
            last_seen_at=now,
        )
        db.add(tu)
        db.flush()
    else:
        if body.name:
            tu.name = body.name
        if body.mobile:
            tu.mobile = body.mobile
        if body.email:
            tu.email = body.email
        tu.last_seen_at = now
    PortalIngester(db).ensure_identity(tenant, tu)
    db.commit()

    user = PortalUser(
        tenant_id=tenant.id,
        tenant_code=tenant.code,
        tenant_user_id=tu.id,
        external_uid=tu.external_uid,
        name=tu.name or "",
    )
    token, ttl = issue_portal_jwt(user)
    logger.info("portal_token_issued", tenant=tenant.code, tenant_user_id=tu.id)
    return TokenResponse(token=token, expires_in=ttl, tenant_user_id=tu.id, tenant_name=tenant.name)


class MeResponse(BaseModel):
    tenant_code: str
    tenant_name: str
    external_uid: str
    name: str


@router.get("/me", response_model=MeResponse)
def me(
    user: PortalUser = Depends(require_portal_user), db: Session = Depends(get_session)
) -> MeResponse:
    tenant = db.get(Tenant, user.tenant_id)
    return MeResponse(
        tenant_code=user.tenant_code,
        tenant_name=tenant.name if tenant else user.tenant_code,
        external_uid=user.external_uid,
        name=user.name,
    )


# ---- tickets ------------------------------------------------------------------


class PortalTicketOut(BaseModel):
    id: int
    short_code: str
    title: str | None
    stage: str | None
    stage_label: str | None
    stage_tone: str | None
    product_line_code: str | None
    module: str | None
    can_edit: bool
    has_reply: bool
    created_at: datetime
    updated_at: datetime
    stage_changed_at: datetime | None


class StageEvent(BaseModel):
    stage: str
    stage_label: str
    at: datetime


class PortalTicketDetail(PortalTicketOut):
    body: str | None
    reply_content: str | None  # 对客答复（hub 级联缓存）
    timeline: list[StageEvent]


class PortalTicketList(BaseModel):
    items: list[PortalTicketOut]
    total: int
    page: int
    page_size: int


class CreateTicketBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1, max_length=20000)
    product_line_code: str | None = Field(None, max_length=64)
    module: str | None = Field(None, max_length=128)
    extra: dict[str, Any] | None = None  # 租户侧附加上下文（页面/版本等），原样存 source_payload


class UpdateTicketBody(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=512)
    body: str | None = Field(None, min_length=1, max_length=20000)


class SupplementBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)


class StatsResponse(BaseModel):
    total: int
    open: int
    closed: int
    by_stage: dict[str, int]


def _to_out(t: Ticket) -> PortalTicketOut:
    return PortalTicketOut(
        id=t.id,
        short_code=t.short_code,
        title=t.title,
        stage=t.stage,
        stage_label=stage_label(t.stage),
        stage_tone=STAGE_TONE.get(t.stage or "", None),
        product_line_code=t.product_line_code,
        module=t.module,
        can_edit=(t.stage in EDITABLE_STAGES),
        has_reply=bool(t.cached_reply_content),
        created_at=t.created_at,
        updated_at=t.updated_at,
        stage_changed_at=t.stage_changed_at,
    )


def _own_ticket(db: Session, user: PortalUser, ticket_id: int) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if t is None or t.deleted_at is not None or t.tenant_user_id != user.tenant_user_id:
        raise HTTPException(status_code=404, detail="ticket not found")
    return t


def _run_post_ingest(ticket_id: int) -> None:
    # 延迟 import：webhooks 依赖 tickets → 避免模块加载环
    from app.api.webhooks import run_post_ingest_agents

    run_post_ingest_agents(ticket_id)


@router.post("/tickets", response_model=PortalTicketOut, status_code=201)
def create_ticket(
    body: CreateTicketBody,
    background_tasks: BackgroundTasks,
    user: PortalUser = Depends(require_portal_user),
    db: Session = Depends(get_session),
) -> PortalTicketOut:
    tenant = db.get(Tenant, user.tenant_id)
    tu = db.get(TenantUser, user.tenant_user_id)
    if tenant is None or tu is None or not tenant.is_active:
        raise HTTPException(status_code=401, detail="tenant inactive")
    db.info["stage_actor"] = f"portal:{user.tenant_code}"
    res = PortalIngester(db).ingest(
        tenant=tenant,
        tenant_user=tu,
        title=body.title,
        body=body.body,
        product_line_code=body.product_line_code,
        module=body.module,
        extra=body.extra,
    )
    db.commit()
    background_tasks.add_task(_run_post_ingest, res.ticket_id)
    t = db.get(Ticket, res.ticket_id)
    assert t is not None
    logger.info(
        "portal_ticket_created",
        ticket_id=t.id,
        short_code=t.short_code,
        tenant=user.tenant_code,
        trace_id=get_trace_id(),
    )
    return _to_out(t)


@router.get("/tickets", response_model=PortalTicketList)
def list_my_tickets(
    user: PortalUser = Depends(require_portal_user),
    db: Session = Depends(get_session),
    stage: str | None = Query(None),
    stages: list[str] | None = Query(None),
    q: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortalTicketList:
    base = select(Ticket).where(
        Ticket.tenant_user_id == user.tenant_user_id, Ticket.deleted_at.is_(None)
    )
    count = select(func.count(Ticket.id)).where(
        Ticket.tenant_user_id == user.tenant_user_id, Ticket.deleted_at.is_(None)
    )
    wanted = [s for s in (stages or ([stage] if stage else [])) if s in TICKET_STAGES]
    if wanted:
        base = base.where(Ticket.stage.in_(wanted))
        count = count.where(Ticket.stage.in_(wanted))
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = f"%{esc}%"
        base = base.where(
            Ticket.title.ilike(pat, escape="\\") | Ticket.short_code.ilike(pat, escape="\\")
        )
        count = count.where(
            Ticket.title.ilike(pat, escape="\\") | Ticket.short_code.ilike(pat, escape="\\")
        )
    total = int(db.execute(count).scalar() or 0)
    rows = (
        db.execute(
            base.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return PortalTicketList(
        items=[_to_out(t) for t in rows], total=total, page=page, page_size=page_size
    )


def _timeline(db: Session, t: Ticket) -> list[StageEvent]:
    events: list[StageEvent] = [
        StageEvent(
            stage="received", stage_label=stage_label("received") or "received", at=t.received_at
        )
    ]
    rows = StatusHistoryRepository(db).find_for_entity(entity_type="ticket_stage", entity_id=t.id)
    for r in rows:
        if r.to_status == "received":
            continue
        events.append(
            StageEvent(
                stage=r.to_status,
                stage_label=stage_label(r.to_status) or r.to_status,
                at=r.changed_at,
            )
        )
    return events


@router.get("/tickets/{ticket_id}", response_model=PortalTicketDetail)
def get_my_ticket(
    ticket_id: int,
    user: PortalUser = Depends(require_portal_user),
    db: Session = Depends(get_session),
) -> PortalTicketDetail:
    t = _own_ticket(db, user, ticket_id)
    out = _to_out(t)
    return PortalTicketDetail(
        **out.model_dump(),
        body=t.body,
        reply_content=t.cached_reply_content,
        timeline=_timeline(db, t),
    )


@router.patch("/tickets/{ticket_id}", response_model=PortalTicketOut)
def update_my_ticket(
    ticket_id: int,
    body: UpdateTicketBody,
    user: PortalUser = Depends(require_portal_user),
    db: Session = Depends(get_session),
) -> PortalTicketOut:
    t = _own_ticket(db, user, ticket_id)
    if t.stage not in EDITABLE_STAGES:
        raise HTTPException(
            status_code=409,
            detail=f"当前阶段「{stage_label(t.stage)}」不可修改；如需补充信息请使用补充资料",
        )
    if body.title is None and body.body is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    changes: list[str] = []
    if body.title is not None and body.title.strip() != (t.title or ""):
        t.title = body.title.strip()
        changes.append("标题")
    if body.body is not None and body.body.strip() != (t.body or ""):
        t.body = body.body.strip()
        changes.append("正文")
    if changes:
        StatusHistoryRepository(db).record(
            entity_type="ticket",
            entity_id=t.id,
            from_status=t.status,
            to_status=t.status,
            changed_by=f"portal:{user.tenant_code}",
            reason=f"提单人修改{'/'.join(changes)}",
            metadata={"action": "portal_update", "external_uid": user.external_uid},
        )
        db.commit()
    return _to_out(t)


@router.post("/tickets/{ticket_id}/supplement", response_model=PortalTicketOut)
def supplement_my_ticket(
    ticket_id: int,
    body: SupplementBody,
    user: PortalUser = Depends(require_portal_user),
    db: Session = Depends(get_session),
) -> PortalTicketOut:
    t = _own_ticket(db, user, ticket_id)
    if t.stage in TERMINAL_STAGES:
        raise HTTPException(
            status_code=409, detail=f"工单已「{stage_label(t.stage)}」，不可再补充；请新建工单"
        )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    t.body = f"{t.body or ''}\n\n[提单人补充 @{stamp}]\n{body.content.strip()}"
    db.info["stage_actor"] = f"portal:{user.tenant_code}"
    StatusHistoryRepository(db).record(
        entity_type="ticket",
        entity_id=t.id,
        from_status=t.status,
        to_status=t.status,
        changed_by=f"portal:{user.tenant_code}",
        reason="提单人补充资料",
        metadata={"action": "portal_supplement", "external_uid": user.external_uid},
    )
    # 补料态：提单人补完 → 自动回到处理中，让自动答复链/处理人重新接手
    if t.hub_issue_id is not None:
        hub = db.get(HubIssue, t.hub_issue_id)
        if hub is not None and hub.op_status == OP_SUPPLEMENTING:
            apply_op_status(
                db,
                hub,
                to_status=OP_PROCESSING,
                handler=hub.op_handler or "agent",
                reason="提单人已补充资料",
            )
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.get("/stats", response_model=StatsResponse)
def my_stats(
    user: PortalUser = Depends(require_portal_user), db: Session = Depends(get_session)
) -> StatsResponse:
    rows = db.execute(
        select(Ticket.stage, func.count(Ticket.id))
        .where(Ticket.tenant_user_id == user.tenant_user_id, Ticket.deleted_at.is_(None))
        .group_by(Ticket.stage)
    ).all()
    by_stage: dict[str, int] = {}
    total = 0
    closed = 0
    for stage, n in rows:
        key = stage or "received"
        by_stage[key] = by_stage.get(key, 0) + int(n)
        total += int(n)
        if key in TERMINAL_STAGES:
            closed += int(n)
    return StatsResponse(total=total, open=total - closed, closed=closed, by_stage=by_stage)


__all__ = ["router"]
