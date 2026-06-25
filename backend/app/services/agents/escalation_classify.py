"""Escalation二次分类 agent (D4 第③段) — classify AI-CS-failed tickets.

When a customer isn't satisfied with the AI customer-service answer, the
escalation lands as a Raw ticket (source='ai_cs'). This agent re-classifies
it using the GOLDEN TRIPLE — original question + AI's answer + dissatisfaction
feedback — which is a far stronger signal than a bare ticket:

    AI gave correct steps + "did it, still broken" → Bug_fix
    AI said "not supported" + "then support it"   → Demand
    AI answered the wrong thing + customer restates → Operation (人工)

Mirrors classify.py: LLM via router → validate type → write tickets.predicted_*
+ agent_decisions(decision_type='classify_type', agent='escalation_classify_v1').
The escalation context lives in ticket.source_payload['ai_cs'] (set by the
cs-escalation webhook); this agent reads it from there.

Gated nowhere by itself — the webhook decides to call it. Auto-graduation uses
a SEPARATE higher bar (ESCALATION_AUTO_CONFIDENCE) because this chain builds a
hub_issue and pushes Linear.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket

logger = get_logger(__name__)

_VALID_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task"})
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


def _prompt_version() -> str:
    return get_settings().escalation_prompt_version


def _load_system_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return load_prompt(f"escalation_classify_{_prompt_version()}")


@dataclass(slots=True, frozen=True)
class EscalationTriple:
    original_question: str
    ai_answer: str
    dissatisfaction: str


@dataclass(slots=True, frozen=True)
class EscalationResult:
    type: str
    confidence: float
    reason: str
    cost_usd: float
    model: str


class EscalationClassifyError(Exception):
    """LLM output couldn't be parsed/validated."""


def triple_from_ticket(ticket: Ticket) -> EscalationTriple:
    """Read the golden triple from source_payload['ai_cs'] (set by webhook),
    falling back to the ticket body for the question."""
    payload: dict[str, Any] = {}
    if isinstance(ticket.source_payload, dict):
        payload = ticket.source_payload.get("ai_cs") or {}
    return EscalationTriple(
        original_question=str(
            payload.get("original_question") or ticket.body or ticket.title or ""
        ),
        ai_answer=str(payload.get("ai_answer") or ""),
        dissatisfaction=str(payload.get("dissatisfaction") or ""),
    )


def classify_escalation_payload(
    triple: EscalationTriple,
    *,
    product_line_code: str | None = None,
    module: str | None = None,
    extra_context: str = "",
    router: LLMRouter | None = None,
) -> EscalationResult:
    router = router or LLMRouter.from_settings()
    user_prompt = (
        f"客户原始问题：{triple.original_question}\n"
        f"AI 客服回答：{triple.ai_answer}\n"
        f"客户不满反馈：{triple.dissatisfaction}\n"
        f"产品线/模块：{product_line_code or ''}/{module or ''}"
    )
    if extra_context:
        user_prompt += f"\n[附件识别]：{extra_context}"
    resp = router.complete(
        [
            LLMMessage(role="system", content=_load_system_prompt()),
            LLMMessage(role="user", content=user_prompt),
        ],
        agent=f"escalation_classify_{_prompt_version()}",
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = _parse(resp.content)
    return EscalationResult(
        type=parsed["type"],
        confidence=float(parsed["confidence"]),
        reason=str(parsed.get("reason") or ""),
        cost_usd=resp.cost_usd,
        model=resp.model,
    )


def _parse(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise EscalationClassifyError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise EscalationClassifyError(f"expected JSON object, got {type(data).__name__}")
    t = data.get("type")
    if t not in _VALID_TYPES:
        raise EscalationClassifyError(f"invalid type {t!r}; must be one of {sorted(_VALID_TYPES)}")
    try:
        c = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise EscalationClassifyError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= c <= 1.0:
        raise EscalationClassifyError(f"confidence out of range: {c}")
    return data


def classify_escalation_ticket(
    ticket_id: int, db: Session | None = None
) -> EscalationResult | None:
    """BG task body. Returns None on any failure (logged); never raises.
    Writes tickets.predicted_* + an agent_decisions audit row."""
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None
    try:
        t = db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            logger.warning("escalation_classify_ticket_not_found", ticket_id=ticket_id)
            return None
        triple = triple_from_ticket(t)
        try:
            result = classify_escalation_payload(
                triple,
                product_line_code=t.product_line_code,
                module=t.module,
            )
        except (EscalationClassifyError, LLMRouterError) as e:
            logger.warning("escalation_classify_failed", ticket_id=ticket_id, error=str(e))
            return None

        t.predicted_type = result.type
        t.predicted_confidence = Decimal(f"{result.confidence:.2f}")
        t.classified_at = datetime.now(UTC)
        db.add(
            AgentDecision(
                decision_type="classify_type",
                subject_type="ticket",
                subject_id=t.id,
                proposal={
                    "predicted_type": result.type,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "model": result.model,
                    "cost_usd": result.cost_usd,
                    "agent": f"escalation_classify_{_prompt_version()}",
                    "source": "ai_cs_escalation",
                },
            )
        )
        db.commit()
        logger.info(
            "escalation_classify_committed",
            ticket_id=ticket_id,
            short_code=t.short_code,
            predicted_type=result.type,
            confidence=result.confidence,
            cost_usd=result.cost_usd,
        )
        return result
    except Exception:
        if own_session:
            db.rollback()
        logger.exception("escalation_classify_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
