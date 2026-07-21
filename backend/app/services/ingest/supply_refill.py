"""补料回填公共服务（跨源可复用；本期只接 KSM ingester）.

客户补料后源系统重推同一张单，ingester 命中已存在 ticket 且其状态为
`awaiting_supply` 时调本服务：把新补料内容物化进 ticket，复位状态，并清掉
关联 hub 的 auto_reply 审计——好让既有 drain_operation_auto_reply 重扫重答。

纯机械物化，无 LLM。不 commit（调用方负责事务边界）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.sla.workday import BEIJING

logger = get_logger(__name__)

_CHANGED_BY = "system:supply_refill"


def apply_supply_refill(db: Session, ticket: Ticket, new_payload: dict[str, Any]) -> bool:
    """把补料回推的新内容物化进 ticket。前置：ticket.status == "awaiting_supply"（调用方保证）。

    步骤：
      1. source_payload 覆盖为 new_payload（含新补料内容/附件/节点）
      2. body 追加 [补料回填 北京时间] 段（新 content）
      3. status 复位 awaiting_supply → received，写 status_history
      4. 关联 hub：不存在/已删 → 只更新内容；reply_v>=1（已答复矛盾态）→ 只更新内容 +
         记 status_history 留主管；否则清该 hub 的 auto_reply 审计（drain 重扫）
    返回 True。
    """
    history = StatusHistoryRepository(db)
    new_content = str(new_payload.get("content") or "").strip()

    ticket.source_payload = new_payload
    stamp = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
    if new_content:
        prev_body = ticket.body or ""
        ticket.body = f"{prev_body}\n\n[补料回填 {stamp}]\n{new_content}".strip()

    prev_status = ticket.status
    ticket.status = "received"
    history.record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=prev_status,
        to_status="received",
        changed_by=_CHANGED_BY,
        reason="补料回填：客户补充资料，工单复位待重新处理",
    )

    hub = db.get(HubIssue, ticket.hub_issue_id) if ticket.hub_issue_id else None
    if hub is None or hub.deleted_at is not None:
        logger.info("supply_refill_no_hub", ticket_id=ticket.id)
        return True

    if hub.reply_content_version >= 1:
        # 已答复矛盾态：不清审计（不覆盖已发答复），记审计留主管
        history.record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=hub.status,
            to_status=hub.status,
            changed_by=_CHANGED_BY,
            reason="补料回填但 hub 已答复（reply_v>=1），留主管人工判断",
        )
        logger.info("supply_refill_already_replied", ticket_id=ticket.id, hub_issue_id=hub.id)
        return True

    cleared = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.decision_type == "auto_reply",
            AgentDecision.subject_type == "hub_issue",
            AgentDecision.subject_id == hub.id,
        )
        .all()
    )
    for d in cleared:
        db.delete(d)
    if new_content:
        prev_canonical = hub.canonical_body or ""
        hub.canonical_body = f"{prev_canonical}\n\n[补料回填 {stamp}]\n{new_content}".strip()
    logger.info(
        "supply_refill_cleared_audit",
        ticket_id=ticket.id,
        hub_issue_id=hub.id,
        cleared=len(cleared),
    )
    return True
