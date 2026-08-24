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
from app.api.history_labels import (
    collect_user_ids,
    humanize_actor,
    humanize_reason,
    humanize_status,
    load_user_names,
)
from app.api.ksm_nodes import KsmNode, parse_ksm_nodes
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
from app.services.attachments.thumbnail import THUMB_MIME, make_thumbnail

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
    assigned_user_id: int | None  # 责任人（路由分工）
    assigned_user_name: str | None = None
    handler_user_id: int | None = None  # 处理人（当前实际持有人）
    handler_user_name: str | None = None
    predicted_type: str | None = None
    hub_issue_id: int | None
    op_status: str | None = (
        None  # 所挂 hub_issue 的 Operation 状态机（仅 Operation 有值，研发类为空）
    )
    hub_status: str | None = (
        None  # 所挂 hub_issue 的 status（研发类处理状态判定：released=处理完成，其余=处理中）
    )
    linear_status: str | None = (
        None  # 研发类所挂 hub 的 linear_status（镜像 Linear 列名）；「研发进度」列译中文展示
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
    # AI 产品模块归类建议（留痕；生效值仍是 product_line_code/module）
    predicted_product_line_code: str | None = None
    predicted_module: str | None = None
    predicted_module_confidence: float | None = None
    module_classified_at: datetime | None = None
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
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    source_code: str | None = Query(None),
    type: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    assigned_user_id: int | None = Query(None),
    handler_user_ids: list[int] | None = Query(None),  # 处理人多选筛选
    predicted_types: list[str] | None = Query(None),  # AI 分类类型多选筛选（1.2）
    unassigned_only: bool = Query(False),
    customer_identity_id: int | None = Query(None),
    hub_issue_id: int | None = Query(None),
    source_ticket_q: str | None = Query(None),  # 来源工单号/本系统编号子串搜索（全表）
    op_status: str | None = Query(None),  # 处理状态筛选（所挂 hub_issue 的 op_status）
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> TicketListResponse:
    # 行级可见性：admin + supervisor 看全部；其余角色只看处理人=自己的工单
    is_privileged = user.role in ("admin", "supervisor")
    p = TicketRepository(db).list_paginated(
        source_code=source_code,
        type_=type,
        status=status,
        assigned_user_id=assigned_user_id,
        handler_user_ids=handler_user_ids,
        visible_to_user_id=None if is_privileged else user.user_id,
        predicted_types=predicted_types,
        unassigned_only=unassigned_only,
        customer_identity_id=customer_identity_id,
        hub_issue_id=hub_issue_id,
        source_ticket_q=source_ticket_q,
        op_status=op_status,
        page=page,
        page_size=page_size,
    )
    # batch-load user names to avoid N+1（责任人 + 处理人）
    user_ids = {t.assigned_user_id for t in p.items if t.assigned_user_id is not None}
    user_ids |= {t.handler_user_id for t in p.items if t.handler_user_id is not None}
    user_name_map: dict[int, str] = {}
    if user_ids:
        rows = db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
        user_name_map = {r.id: r.name for r in rows}

    # batch-load 所挂 hub_issue 的 op_status（仅 Operation 有值）+ reject_count，避免 N+1
    hub_ids = {t.hub_issue_id for t in p.items if t.hub_issue_id is not None}
    hub_op_map: dict[int, str | None] = {}
    hub_reject_map: dict[int, int] = {}
    hub_status_map: dict[int, str | None] = {}
    # 已毕业工单的产品线/模块以 hub 为准（编辑只改 hub，见 update_hub_attributes）；
    # 列表读 ticket.* 会读到毕业时的旧快照，故这里带出 hub 的值，_to_summary 覆盖。
    hub_plc_map: dict[int, str | None] = {}
    hub_module_map: dict[int, str | None] = {}
    # 研发类（Bug_fix/Demand）所挂 hub 的 linear_status（镜像 Linear 列名，展示层）；
    # 工单列表「研发进度」列据此显示细粒度阶段（前端 linearStatusToCN 译中文）。
    hub_linear_status_map: dict[int, str | None] = {}
    if hub_ids:
        hrows = db.execute(
            select(
                HubIssue.id,
                HubIssue.op_status,
                HubIssue.reject_count,
                HubIssue.status,
                HubIssue.product_line_code,
                HubIssue.module,
                HubIssue.linear_status,
            ).where(HubIssue.id.in_(hub_ids))
        ).all()
        hub_op_map = {r.id: r.op_status for r in hrows}
        hub_reject_map = {r.id: r.reject_count for r in hrows}
        hub_status_map = {r.id: r.status for r in hrows}
        hub_plc_map = {r.id: r.product_line_code for r in hrows}
        hub_module_map = {r.id: r.module for r in hrows}
        hub_linear_status_map = {r.id: r.linear_status for r in hrows}

    # batch-load 主产品名称 + SLA 解决时限（product_line_code → name / sla_resolve_hours）
    # 已毕业工单以 hub 的 product_line_code 为准，故两处 code 都要并入名字查询集合
    pl_codes = {t.product_line_code for t in p.items if t.product_line_code}
    pl_codes |= {code for code in hub_plc_map.values() if code}
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
        if t.handler_user_id is not None:
            s.handler_user_name = user_name_map.get(t.handler_user_id)
        if t.hub_issue_id is not None:
            s.op_status = hub_op_map.get(t.hub_issue_id)
            s.reject_count = hub_reject_map.get(t.hub_issue_id, 0)
            s.hub_status = hub_status_map.get(t.hub_issue_id)
            s.linear_status = hub_linear_status_map.get(t.hub_issue_id)
            # 已毕业：产品线/模块以 hub 为准（与详情页 graduated ? hub.* : ticket.* 对齐）。
            # 编辑参数只改 hub，ticket.* 停留在毕业时快照，故这里覆盖为 hub 的最新值。
            hub_plc = hub_plc_map.get(t.hub_issue_id)
            if hub_plc is not None:
                s.product_line_code = hub_plc
            hub_module = hub_module_map.get(t.hub_issue_id)
            if hub_module is not None:
                s.module = hub_module
        if s.product_line_code:
            s.product_name = product_name_map.get(s.product_line_code)
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
    auth_user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> TicketDetail:
    ticket = TicketRepository(db).get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    # 行级可见性：非 admin/主管 只能看处理人=自己的工单（否则等同不存在）
    if auth_user.role not in ("admin", "supervisor") and ticket.handler_user_id != auth_user.user_id:
        raise HTTPException(status_code=404, detail="ticket not found")
    detail = TicketDetail.model_validate(ticket)
    if ticket.assigned_user_id is not None:
        u = db.get(User, ticket.assigned_user_id)
        detail.assigned_user_name = u.name if u else None
    if ticket.handler_user_id is not None:
        hu = db.get(User, ticket.handler_user_id)
        detail.handler_user_name = hu.name if hu else None
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


# 附件不可变（storage_key 确定性 key，内容不变）→ 长缓存，重开工单/复现同图走浏览器缓存。
_ATTACHMENT_CACHE_CONTROL = "private, max-age=86400, immutable"


def _attachment_response(data: bytes, media_type: str, *, att_id: int, size: str | None) -> Response:
    """统一构造附件响应：带 Cache-Control + ETag（重开不重复全量下载）。"""
    etag = f'"{att_id}-{size or "full"}-{len(data)}"'
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": _ATTACHMENT_CACHE_CONTROL, "ETag": etag},
    )


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
def download_attachment(
    ticket_id: int,
    attachment_id: int,
    size: str | None = Query(None),  # "thumb"=列表缩略图（图片缩到 ~240px），否则原图
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> Response:
    """附件下载代理：优先从 MinIO 拉回（已存档），回落原始 source_url 重新下载。

    统一经此端点，前端不直接暴露 MinIO 内网地址（storage_key）或需鉴权的
    上游 source_url（KSM）。任何一路都拿不到 → 404。

    size="thumb" 时对图片返回缩略图（缓存回 MinIO，下次直接命中），大幅降低列表加载字节。
    """
    att = db.get(Attachment, attachment_id)
    if att is None or att.ticket_id != ticket_id:
        raise HTTPException(status_code=404, detail="attachment not found")

    settings = get_settings()
    want_thumb = size == "thumb" and att.kind == "image"

    # 1) 已存档：从 MinIO 按对象 key 读回（浏览器无法直连内网 MinIO，故走后端代理）。
    if att.storage_key:
        try:
            store = MinioStore(settings)
            key = store.key_from_storage_url(att.storage_key)
            if key is not None:
                # 缩略图：先查缓存对象命中，否则取原图缩放后缓存回 MinIO。
                if want_thumb:
                    thumb = _get_or_make_thumbnail(store, key)
                    if thumb is not None:
                        return _attachment_response(
                            thumb, THUMB_MIME, att_id=attachment_id, size="thumb"
                        )
                data = store.get_bytes(key)
                return _attachment_response(
                    data, _content_type(att, data), att_id=attachment_id, size=size
                )
        except MinioNotConfiguredError:
            pass  # 未配 MinIO → 回落 source_url
        except Exception as e:  # MinIO 读失败（对象丢失等）→ 回落 source_url
            logger.warning("attachment_download_minio_failed", att_id=attachment_id, error=str(e))

    # 2) 回落：从原始 source_url 重新下载（KSM 需鉴权，走 KSMClient；其余走通用 GET）。
    if att.source_url:
        try:
            data = _fetch_source_bytes(att.source_url, settings)
            if want_thumb:
                made = make_thumbnail(data)
                if made is not None:
                    return _attachment_response(
                        made[0], made[1], att_id=attachment_id, size="thumb"
                    )
            return _attachment_response(
                data, _content_type(att, data), att_id=attachment_id, size=size
            )
        except Exception as e:
            logger.warning("attachment_download_source_failed", att_id=attachment_id, error=str(e))

    raise HTTPException(status_code=404, detail="attachment content unavailable")


def _thumb_key(key: str) -> str:
    """原图对象 key → 缩略图 key（同前缀加 .thumb.jpg，与原图并存于 MinIO）。"""
    return f"{key}.thumb.jpg"


def _get_or_make_thumbnail(store: MinioStore, key: str) -> bytes | None:
    """取缩略图：MinIO 缓存命中直接返回；否则取原图缩放 + 缓存回 MinIO。

    缩图失败（非图/损坏）返回 None，调用方回落原图。
    """
    tkey = _thumb_key(key)
    if store.object_exists(tkey):
        try:
            return store.get_bytes(tkey)
        except Exception:
            pass  # 缓存对象读失败 → 重新生成
    data = store.get_bytes(key)
    made = make_thumbnail(data)
    if made is None:
        return None
    thumb_bytes, thumb_mime = made
    try:
        store.put_bytes(tkey, thumb_bytes, thumb_mime)  # 缓存回 MinIO，下次命中
    except Exception as e:
        logger.warning("thumbnail_cache_put_failed", key=tkey, error=str(e))
    return thumb_bytes


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
    # 人性化展示字段（读取层翻译，覆盖历史存量；前端优先用这些）：
    # 中文类型/姓名替换后的 reason、处理人姓名/角色、状态枚举中文。
    reason_display: str | None = None
    actor_display: str | None = None
    from_status_zh: str | None = None
    to_status_zh: str | None = None
    # hub_issue_link fields (None when kind != 'hub_issue_link')
    hub_issue_id: int | None = None
    effective_to: datetime | None = None
    change_reason: str | None = None
    human_confirmed: bool | None = None


class HistoryResponse(BaseModel):
    ticket_id: int
    items: list[HistoryEvent]
    # KSM 工单的源系统流转节点（handleSteps，按时间升序）；非 KSM / 无数据为空。
    # 前端「处理节点」区块：KSM 工单渲染此列表，非 KSM 从 items 映射接收/处理/关闭。
    ksm_nodes: list[KsmNode] = []


@router.get("/{ticket_id}/history", response_model=HistoryResponse)
def get_ticket_history(
    ticket_id: int,
    auth_user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> HistoryResponse:
    ticket = TicketRepository(db).get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    # 行级可见性：非 admin/主管 只能看处理人=自己的工单
    if auth_user.role not in ("admin", "supervisor") and ticket.handler_user_id != auth_user.user_id:
        raise HTTPException(status_code=404, detail="ticket not found")

    status_rows = StatusHistoryRepository(db).find_for_entity(
        entity_type="ticket", entity_id=ticket_id
    )
    relink_rows = TicketHubIssueHistoryRepository(db).find_for_ticket(ticket_id)

    # 处理节点文本人性化（读取层翻译，覆盖历史存量）：先批量查 reason 里 user_id 对应姓名，避免 N+1。
    name_by_id = load_user_names(db, collect_user_ids([s.reason for s in status_rows]))

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
                reason_display=humanize_reason(s.reason, name_by_id),
                actor_display=humanize_actor(s.changed_by, name_by_id),
                from_status_zh=humanize_status(s.from_status),
                to_status_zh=humanize_status(s.to_status),
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
    # KSM 工单：解析源系统流转节点（handleSteps）供前端处理节点区块展示。
    ksm_nodes = parse_ksm_nodes(ticket.source_payload) if ticket.source_code == "ksm" else []
    return HistoryResponse(ticket_id=ticket_id, items=events, ksm_nodes=ksm_nodes)
