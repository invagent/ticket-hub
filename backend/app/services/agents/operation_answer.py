"""Operation 自动答复（ADR-0016 §3「Operation 未命中 → 直接答复客户」）.

Operation hub_issue 毕业后，调 ai_cs agent（replay）生成答复，harness 硬判可发
则走 author_reply 级联回写客户（复用 cascade→outbox→KSM/智齿回写关单），否则
留主管。triage 已分类故不重走 A/B/C/D。escalation(ai_cs) 来源不走此路（走 reflect）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsError
from app.config import Settings, get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.cascade.supply_sync import SupplySyncError, request_supply
from app.services.knowledge_feedback.service import (
    KnowledgeFeedbackDisabledError,
    build_client,
)
from app.services.skills.prompt_store import load_prompt

logger = get_logger(__name__)

_VALID_BRANCHES = frozenset({"C", "D", "transfer"})


@dataclass(slots=True, frozen=True)
class AnswerRoute:
    branch: str  # "C" | "D" | "transfer"
    supply_note: str = ""


def _route_answer(question: str, answer: str, *, router: LLMRouter | None = None) -> AnswerRoute:
    """answer-router LLM 判 C/D/transfer。异常/非法一律兜底 transfer（留主管）。"""
    try:
        prompt = load_prompt("answer_router")
        router = router or LLMRouter.from_settings()
        resp = router.complete(
            [
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content=f"客户问题：{question}\n\nagent 答复：{answer}"),
                LLMMessage(role="user", content="只输出 JSON。"),
            ],
            agent="answer_router",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.content)
        branch = str(data.get("branch") or "").strip()
        if branch not in _VALID_BRANCHES:
            return AnswerRoute(branch="transfer")
        return AnswerRoute(branch=branch, supply_note=str(data.get("supply_note") or "").strip())
    except (LLMRouterError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning("answer_router_failed", error=str(e))
        return AnswerRoute(branch="transfer")


def _record_decision(
    db: Session,
    hub_id: int,
    *,
    branch: str,
    question: str,
    answer: str,
    supply_note: str,
) -> None:
    """写 agent_decisions 审计（auto_reply）。内部 commit。"""
    db.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=hub_id,
            proposal={
                "branch": branch,
                "question": question,
                "answer": answer,
                "supply_note": supply_note,
            },
        )
    )
    db.commit()


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
    except AiCsError as e:
        logger.warning("operation_auto_reply_replay_failed", hub_issue_id=hub.id, error=str(e))
        return False
    finally:
        client.close()

    # answer-router LLM 判 C/D/transfer
    route = _route_answer(question, answer)

    if route.branch == "D":
        try:
            author_reply(db, hub.id, content=answer, authored_by="agent:ai_cs")
        except ReplySyncError as e:
            logger.warning("operation_auto_reply_author_failed", hub_issue_id=hub.id, error=str(e))
            return False
        _record_decision(db, hub.id, branch="D", question=question, answer=answer, supply_note="")
        logger.info("operation_auto_reply_sent", hub_issue_id=hub.id)
        return True

    if route.branch == "C":
        note = route.supply_note or answer
        try:
            request_supply(db, hub.id, note=note, requested_by="agent:ai_cs")
        except SupplySyncError as e:
            logger.warning("operation_auto_supply_failed", hub_issue_id=hub.id, error=str(e))
            return False
        _record_decision(db, hub.id, branch="C", question=question, answer=answer, supply_note=note)
        logger.info("operation_auto_supply_sent", hub_issue_id=hub.id)
        return True

    # transfer → 留主管
    logger.info("operation_auto_reply_transfer", hub_issue_id=hub.id)
    return False
