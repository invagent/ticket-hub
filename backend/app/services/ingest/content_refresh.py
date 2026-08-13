"""内容刷新公共服务（跨源可复用；本期只接 KSM ingester）.

源系统对同一张单重推新内容（客户补料 / 驳回重提）时，ingester 命中已存在
ticket 后调本服务：把新内容物化进 ticket，并同步进关联 hub 的 canonical_body，
好让后续重答/重处理看到最新问题描述。

纯机械物化，无 LLM，不判断/不改 op_status（op_status 转换由调用方 ingester
按分流结果决定）。不 commit（调用方负责事务边界）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.storage.minio_store import classify_attachment_kind, filename_from_url
from app.models import Attachment, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.sla.workday import BEIJING

logger = get_logger(__name__)

_CHANGED_BY = "system:content_refresh"


def apply_content_refresh(db: Session, ticket: Ticket, new_payload: dict[str, Any]) -> bool:
    """把源系统重推的新内容物化进 ticket（+ 同步进关联 hub.canonical_body）。

    步骤：
      1. source_payload 覆盖为 new_payload（含新内容/附件/节点）
      2. body 追加 [内容更新 北京时间] 段（新 content）
      3. new_payload 带 attachment_urls → 追加建新 Attachment 行（保留历史批次，
         不去重不覆盖；走附件流水线 vision_status='queued'）
      4. 写 ticket status_history（from/to 均为当前 status，仅留痕内容更新事件）
      5. 关联 hub 存在且未删 → canonical_body 同步追加同一段
    返回 True。不改 op_status（由调用方决定）。
    """
    history = StatusHistoryRepository(db)
    new_content = str(new_payload.get("content") or "").strip()

    ticket.source_payload = new_payload
    stamp = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
    if new_content:
        prev_body = ticket.body or ""
        ticket.body = f"{prev_body}\n\n[内容更新 {stamp}]\n{new_content}".strip()

    # 补料重推带的新附件：追加建行（保留历史批次，可对比客户前后交了什么）。
    # 仅建行不下载——下载+OCR 由异步附件流水线按 vision_status='queued' 处理。
    for url in new_payload.get("attachment_urls", []) or []:
        db.add(
            Attachment(
                ticket_id=ticket.id,
                source_url=url,
                filename=filename_from_url(url),
                kind=classify_attachment_kind(url),
                vision_status="queued",
            )
        )

    history.record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=ticket.status,
        to_status=ticket.status,
        changed_by=_CHANGED_BY,
        reason="源系统重推新内容",
    )

    hub = db.get(HubIssue, ticket.hub_issue_id) if ticket.hub_issue_id else None
    if hub is None or hub.deleted_at is not None:
        logger.info("content_refresh_no_hub", ticket_id=ticket.id)
        return True

    if new_content:
        prev_canonical = hub.canonical_body or ""
        hub.canonical_body = f"{prev_canonical}\n\n[内容更新 {stamp}]\n{new_content}".strip()
    logger.info("content_refresh_applied", ticket_id=ticket.id, hub_issue_id=hub.id)
    return True
