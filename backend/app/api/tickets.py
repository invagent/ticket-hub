"""GET /api/tickets — list / detail / history.

  GET /api/tickets?source_code=&type=&status=&assigned_user_id=&unassigned_only=&page=&page_size=
  GET /api/tickets/{ticket_id}
  GET /api/tickets/{ticket_id}/history          status + relink merged timeline

All authenticated users can read (any role). D2 may add row-level visibility
(only own + supervisor sees subordinates) — for D1 keep open within the org.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.config import get_settings
from app.core.logging import get_logger
from app.core.storage.minio_store import (
    MinioNotConfiguredError,
    MinioStore,
    guess_content_type,
)
from app.db import get_session
from app.models import Attachment, Customer, CustomerIdentity, HubIssue, ProductLine, User
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.repositories.ticket_hub_issue_history import TicketHubIssueHistoryRepository

router = APIRouter()
logger = get_logger(__name__)


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
    op_status: str | None = (
        None  # 所挂 hub_issue 的 Operation 状态机（仅 Operation 有值，研发类为空）
    )
    product_name: str | None = None  # 主产品名称（product_line_code → product_lines.name）
    reject_count: int = 0  # 客户驳回次数（所挂 hub_issue 的 reject_count；研发类/无 hub 为 0）
    children_count: int = 1  # 关联任务数（拆分子单数；单问题=1，Parent=children_ticket_ids 长度）
    # 提单快照 + SLA（2026-08-04）
    reporter_name: str | None = None  # 提单人姓名（reporter.name）
    reporter_mobile: str | None = None  # 提单人手机（reporter.mobile）
    reporter_email: str | None = None  # 提单人邮箱（reporter.email）
    reporter_company: str | None = None  # 提单公司名称
    reporter_tax_no: str | None = None  # 提单公司税号（上游 payload 暂不带，多为空）
    reporter_tenant: str | None = None  # 归属租户（上游 payload 暂不带，多为空）
    service_level: str | None = "标准服务"  # 服务等级（空→标准服务，_to_summary 填默认）
    remaining_hours: float | None = None  # 剩余处理时间（h，负=已超时；无 received_at/时限时 None）
    updated_at: datetime | None = None  # 工单最后更新时间
    received_at: datetime | None
    customer_replied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AttachmentOut(BaseModel):
    """工单附件（attachments 表行）——前端「工单描述」附件区展示用。

    download_url 指向后端代理端点，从 MinIO 拉流回吐（存档过的）或回落原始 source_url
    重新下载。前端不直接暴露 MinIO 内网地址 / 需鉴权的 source_url。
    """

    id: int
    filename: str | None
    kind: str
    mime: str | None
    size_bytes: int | None
    vision_status: str
    extracted_text: str | None  # OCR 结果（无 key 时为空）
    download_url: str

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
    attachments: list[AttachmentOut] = []  # 附件表记录（智齿/KSM/ai_cs 同步的截图等）


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
    assigned_user_ids: list[int] | None = Query(None),  # 处理人多选筛选（1.1）
    predicted_types: list[str] | None = Query(None),  # AI 分类类型多选筛选（1.2）
    unassigned_only: bool = Query(False),
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
        assigned_user_ids=assigned_user_ids,
        predicted_types=predicted_types,
        unassigned_only=unassigned_only,
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

    # batch-load 所挂 hub_issue 的 op_status（仅 Operation 有值）+ reject_count，避免 N+1
    hub_ids = {t.hub_issue_id for t in p.items if t.hub_issue_id is not None}
    hub_op_map: dict[int, str | None] = {}
    hub_reject_map: dict[int, int] = {}
    if hub_ids:
        hrows = db.execute(
            select(HubIssue.id, HubIssue.op_status, HubIssue.reject_count).where(
                HubIssue.id.in_(hub_ids)
            )
        ).all()
        hub_op_map = {r.id: r.op_status for r in hrows}
        hub_reject_map = {r.id: r.reject_count for r in hrows}

    # batch-load 主产品名称 + SLA 解决时限（product_line_code → name / sla_resolve_hours）
    pl_codes = {t.product_line_code for t in p.items if t.product_line_code}
    product_name_map: dict[str, str] = {}
    pl_resolve_hours_map: dict[str, int | None] = {}
    if pl_codes:
        prows = db.execute(
            select(ProductLine.code, ProductLine.name, ProductLine.sla_resolve_hours).where(
                ProductLine.code.in_(pl_codes)
            )
        ).all()
        product_name_map = {r.code: r.name for r in prows}
        pl_resolve_hours_map = {r.code: r.sla_resolve_hours for r in prows}

    now = datetime.now(UTC)

    def _remaining_hours(t: Any) -> float | None:
        """剩余处理时间(h)：received_at + 时限 - now。负=已超时。无 received_at/时限→None。
        时限优先 ticket.sla_standard_hours（飞书回填），否则产品线 sla_resolve_hours。"""
        if t.received_at is None:
            return None
        limit_h: float | None = None
        if t.sla_standard_hours is not None:
            limit_h = float(t.sla_standard_hours)
        elif t.product_line_code:
            rh = pl_resolve_hours_map.get(t.product_line_code)
            limit_h = float(rh) if rh is not None else None
        if limit_h is None:
            return None
        received = t.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        deadline = received + timedelta(hours=limit_h)
        return float(round((deadline - now).total_seconds() / 3600.0, 1))

    def _to_summary(t: Any) -> TicketSummary:
        s = TicketSummary.model_validate(t)
        if t.assigned_user_id is not None:
            s.assigned_user_name = user_name_map.get(t.assigned_user_id)
        if t.hub_issue_id is not None:
            s.op_status = hub_op_map.get(t.hub_issue_id)
            s.reject_count = hub_reject_map.get(t.hub_issue_id, 0)
        if t.product_line_code:
            s.product_name = product_name_map.get(t.product_line_code)
        # 关联任务数：拆分子单数（Parent 持有 children_ticket_ids）；单问题工单=1
        s.children_count = len(t.children_ticket_ids or []) or 1
        # 提单人信息从 reporter JSON 解析（入库写的是 name/mobile/email）
        rep = t.reporter or {}
        s.reporter_name = rep.get("name") or None
        s.reporter_mobile = rep.get("mobile") or None
        s.reporter_email = rep.get("email") or None
        # 服务等级空 → 标准服务
        s.service_level = t.service_level or "标准服务"
        s.remaining_hours = _remaining_hours(t)
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
        # 提单人信息从 reporter JSON 解析（入库写的是 name/mobile/email，见 zhichi_ingester）。
        # 与列表接口 _to_summary 口径一致；feedback_user/linkman 作旧数据兜底。
        rep = ticket.reporter
        detail.reporter_name = rep.get("name") or rep.get("feedback_user") or rep.get("linkman")
        detail.reporter_mobile = rep.get("mobile")
        detail.reporter_email = rep.get("email")
    # 附件（attachments 表）：智齿 file_str / KSM / ai_cs 同步下来的截图等。
    # download_url 走后端代理端点，前端不碰 MinIO 内网地址 / 需鉴权的原始 URL。
    atts = (
        db.execute(
            select(Attachment)
            .where(Attachment.ticket_id == ticket_id)
            .order_by(Attachment.id)
        )
        .scalars()
        .all()
    )
    detail.attachments = [
        AttachmentOut(
            id=a.id,
            filename=a.filename,
            kind=a.kind,
            mime=a.mime,
            size_bytes=a.size_bytes,
            vision_status=a.vision_status,
            extracted_text=a.extracted_text,
            download_url=f"/api/tickets/{ticket_id}/attachments/{a.id}/download",
        )
        for a in atts
    ]
    return detail


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
def download_attachment(
    ticket_id: int,
    attachment_id: int,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> Response:
    """附件下载代理：优先从 MinIO 拉回（已存档），回落原始 source_url 重新下载。

    统一经此端点，前端不直接暴露 MinIO 内网地址（storage_key）或需鉴权的
    上游 source_url（KSM）。任何一路都拿不到 → 404。
    """
    att = db.get(Attachment, attachment_id)
    if att is None or att.ticket_id != ticket_id:
        raise HTTPException(status_code=404, detail="attachment not found")

    settings = get_settings()

    # 1) 已存档：从 MinIO 按对象 key 读回（浏览器无法直连内网 MinIO，故走后端代理）。
    if att.storage_key:
        try:
            store = MinioStore(settings)
            key = store.key_from_storage_url(att.storage_key)
            if key is not None:
                data = store.get_bytes(key)
                return Response(content=data, media_type=_content_type(att, data))
        except MinioNotConfiguredError:
            pass  # 未配 MinIO → 回落 source_url
        except Exception as e:  # MinIO 读失败（对象丢失等）→ 回落 source_url
            logger.warning("attachment_download_minio_failed", att_id=attachment_id, error=str(e))

    # 2) 回落：从原始 source_url 重新下载（KSM 需鉴权，走 KSMClient；其余走通用 GET）。
    if att.source_url:
        try:
            data = _fetch_source_bytes(att.source_url, settings)
            return Response(content=data, media_type=_content_type(att, data))
        except Exception as e:
            logger.warning("attachment_download_source_failed", att_id=attachment_id, error=str(e))

    raise HTTPException(status_code=404, detail="attachment content unavailable")


def _content_type(att: Attachment, data: bytes) -> str:
    """推断响应 Content-Type：att.mime 优先，否则走共享 guess_content_type。

    历史附件（如智齿早期入库）mime 常为空，返回 octet-stream 会让 <img> 不渲染，
    故补推断（含 ofd/log/xml/txt 映射 + 字节 magic），让文件正确按类型返回。
    """
    if att.mime:
        return att.mime
    return guess_content_type(
        filename=att.filename, source_url=att.source_url, kind=att.kind, data=data
    )


def _fetch_source_bytes(source_url: str, settings: Any) -> bytes:
    """从原始 URL 拉附件字节。KSM 附件走 KSMClient（带鉴权），其余走通用 httpx GET。

    通用路径也伪装浏览器 UA——智齿 sobot 图床等对默认 UA 可能拒绝（同 KSM 套路）。
    """
    if "kingdee" in source_url or "ierp" in source_url:
        from adapters.ksm.client import KSMClient
        from adapters.ksm.types import KSMConfig

        return KSMClient(KSMConfig.from_settings(settings)).download_attachment(source_url)
    import httpx

    resp = httpx.get(
        source_url,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    return resp.content


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
