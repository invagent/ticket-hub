"""Ticket classification agent (D3-C).

Pipeline:
    1. Read prompts/classify_v1.md as the system prompt
    2. Compose user prompt from ticket fields (title/body/product_line/module)
    3. Call LLMRouter.complete with response_format=json_object
    4. Parse JSON {type, confidence, reason}
    5. Validate type ∈ Operation/Bug_fix/Demand/Internal_task
    6. Write back to tickets.predicted_type / predicted_confidence /
       classified_at + structured log

Designed to run as FastAPI BackgroundTask after webhook ingest, so failures
don't block the webhook ack — they just mean classification is missing
(re-runnable later).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket

logger = get_logger(__name__)


_VALID_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task"})

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# ADR-0016 P1：skill 名固定（版本走 skill_prompts 三槽 draft/current/previous），
# 不再有 classify_v1/v2 双名双轨。
_SKILL_NAME = "classify"


def _load_system_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return load_prompt(_SKILL_NAME)


@dataclass(slots=True, frozen=True)
class ClassifyResult:
    type: str
    confidence: float
    reason: str
    cost_usd: float
    model: str
    raw: dict[str, Any]


class ClassifyError(Exception):
    """LLM call succeeded but output couldn't be parsed/validated."""


def classify_payload(
    *,
    title: str | None,
    body: str | None,
    product_line_code: str | None,
    module: str | None,
    router: LLMRouter | None = None,
    system_prompt_override: str | None = None,
) -> ClassifyResult:
    """Pure function: takes ticket fields, returns ClassifyResult.

    Caller responsible for persistence. Tested in isolation with mocked router.
    system_prompt_override 供 draft 验证器注入候选提示词（不落库、不影响生产）。
    """
    router = router or LLMRouter.from_settings()
    user_prompt = _format_user_prompt(
        title=title or "",
        body=body or "",
        product_line=product_line_code or "",
        module=module or "",
    )
    resp = router.complete(
        [
            LLMMessage(role="system", content=system_prompt_override or _load_system_prompt()),
            LLMMessage(role="user", content=user_prompt),
        ],
        agent=_SKILL_NAME if system_prompt_override is None else f"{_SKILL_NAME}:draft",
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    parsed = _parse_response(resp.content)
    return ClassifyResult(
        type=parsed["type"],
        confidence=float(parsed["confidence"]),
        reason=str(parsed.get("reason") or ""),
        cost_usd=resp.cost_usd,
        model=resp.model,
        raw=resp.raw,
    )


def _format_user_prompt(*, title: str, body: str, product_line: str, module: str) -> str:
    # Trim body — long工单 body wastes tokens and rarely changes the verdict.
    snippet = (body or "")[:1500]
    return f"title={title!r}\nproduct_line={product_line!r}, module={module!r}\nbody={snippet!r}"


def _parse_response(content: str) -> dict[str, Any]:
    """Extract & validate the JSON envelope. GLM with response_format
    json_object should give clean JSON, but we still defend against
    edge cases (missing keys, unexpected type)."""
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise ClassifyError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise ClassifyError(f"expected JSON object, got {type(data).__name__}")
    t = data.get("type")
    if t not in _VALID_TYPES:
        raise ClassifyError(f"invalid type {t!r}; must be one of {sorted(_VALID_TYPES)}")
    try:
        c = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise ClassifyError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= c <= 1.0:
        raise ClassifyError(f"confidence out of range: {c}")
    return data


def classify_ticket(ticket_id: int, db: Session | None = None) -> ClassifyResult | None:
    """BackgroundTask body. Returns None on any failure (logged); never
    raises so failures don't crash the worker thread.

    Caller can pass `db` to reuse a session (tests do this); otherwise we
    open a fresh session via make_session().
    """
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None

    try:
        t = db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            logger.warning("classify_ticket_not_found", ticket_id=ticket_id)
            return None
        try:
            result = classify_payload(
                title=t.title,
                body=t.body,
                product_line_code=t.product_line_code,
                module=t.module,
            )
        except (ClassifyError, LLMRouterError) as e:
            logger.warning(
                "classify_ticket_failed",
                ticket_id=ticket_id,
                error=str(e),
            )
            return None
        # Persist (ticket fields + audit row, single transaction)
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
                    "skill": _SKILL_NAME,
                },
            )
        )
        db.commit()
        logger.info(
            "classify_ticket_committed",
            ticket_id=ticket_id,
            short_code=t.short_code,
            predicted_type=result.type,
            predicted_confidence=result.confidence,
            cost_usd=result.cost_usd,
            model=result.model,
        )
        return result
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("classify_ticket_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
