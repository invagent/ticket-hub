"""Operation 自动答复（ADR-0016 §3「Operation 未命中 → 直接答复客户」）.

Operation hub_issue 毕业后，调 ai_cs agent（replay）生成答复，harness 硬判可发
则走 author_reply 级联回写客户（复用 cascade→outbox→KSM/智齿回写关单），否则
留主管。triage 已分类故不重走 A/B/C/D。escalation(ai_cs) 来源不走此路（走 reflect）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsError
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.knowledge_feedback.service import (
    KnowledgeFeedbackDisabledError,
    build_client,
)

logger = get_logger(__name__)

_TRANSFER_HINTS = ("转人工", "无法回答", "无法处理", "请联系", "人工客服")


def _is_answer_sendable(answer: str, min_length: int) -> bool:
    """harness 硬判：答复能否直接发给客户。"""
    a = (answer or "").strip()
    if len(a) < min_length:
        return False
    return not any(h in a for h in _TRANSFER_HINTS)


def auto_answer_operation(
    db: Session, hub_issue_id: int, *, settings: Settings | None = None
) -> bool:
    """对新毕业的 Operation hub_issue 自动答复。True=已答复，False=留主管。"""
    settings = settings or get_settings()
    if not settings.operation_auto_reply_enabled:
        return False

    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None or hub.type != "Operation":
        return False

    # escalation(ai_cs) 来源不自动答复（走 reflect 反思队列）
    linked = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).first()
    )
    if linked is not None and linked.source_code == "ai_cs":
        return False

    try:
        client = build_client(settings)
    except KnowledgeFeedbackDisabledError:
        logger.info("operation_auto_reply_ai_cs_disabled", hub_issue_id=hub.id)
        return False

    product = hub.product or hub.product_line_code or ""
    module = hub.module or ""
    body = hub.canonical_body or hub.title or ""
    question = f"{product}-{module}：{body}" if module else f"{product}：{body}"
    question = question.lstrip("-：").strip() or body

    try:
        result = client.replay(question=question, use_latest_knowledge=True)
        answer = result.answer
        trace_id = result.trace_id
    except AiCsError as e:
        logger.warning("operation_auto_reply_replay_failed", hub_issue_id=hub.id, error=str(e))
        return False
    finally:
        client.close()

    if not _is_answer_sendable(answer, settings.operation_auto_reply_min_length):
        logger.info(
            "operation_auto_reply_skipped",
            hub_issue_id=hub.id,
            reason="answer not sendable",
            answer_len=len(answer or ""),
        )
        return False

    try:
        author_reply(db, hub.id, content=answer, authored_by="agent:ai_cs")
    except ReplySyncError as e:
        logger.warning("operation_auto_reply_author_failed", hub_issue_id=hub.id, error=str(e))
        return False

    db.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=hub.id,
            proposal={
                "question": question,
                "answer": answer,
                "trace_id": trace_id,
                "sent": True,
            },
        )
    )
    db.commit()
    logger.info("operation_auto_reply_sent", hub_issue_id=hub.id, trace_id=trace_id)
    return True
