"""Conflict-detect agent (D3-D, 决策 13).

Judges whether a freshly-ingested Raw ticket mixes multiple independent
problems and should be split into child tickets.

Pipeline (mirrors classify.py):
    1. Read prompts/conflict_detect_{version}.md as the system prompt
    2. Compose user prompt from ticket fields (title/body/product_line/module)
    3. Call LLMRouter.complete with response_format=json_object
    4. Parse JSON {decision, confidence, reason, sub_issues}
    5. Write an agent_decisions audit row:
       decision_type='split_ticket' (with sub_issues proposal) or 'no_split'

D3-D scope: ADVISORY ONLY — no ticket mutation. The split executor
(services/agents/split.py, no LLM) lands separately and consumes
'split_ticket' proposals. Supervisor can revert rows as usual.

Designed to run as a FastAPI BackgroundTask after webhook ingest; failures
are logged and swallowed so they never block the webhook ack.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket

logger = get_logger(__name__)

_VALID_DECISIONS = frozenset({"split", "no_split"})

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


def _prompt_version() -> str:
    return get_settings().conflict_detect_prompt_version


def _load_system_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return load_prompt(f"conflict_detect_{_prompt_version()}")


@dataclass(slots=True, frozen=True)
class SubIssue:
    title: str
    summary: str


@dataclass(slots=True, frozen=True)
class ConflictResult:
    decision: str  # "split" | "no_split"
    confidence: float
    reason: str
    sub_issues: tuple[SubIssue, ...]
    cost_usd: float
    model: str
    raw: dict[str, Any]


class ConflictDetectError(Exception):
    """LLM call succeeded but output couldn't be parsed/validated."""


def detect_conflict_payload(
    *,
    title: str | None,
    body: str | None,
    product_line_code: str | None,
    module: str | None,
    router: LLMRouter | None = None,
) -> ConflictResult:
    """Pure function: takes ticket fields, returns ConflictResult.

    Caller responsible for persistence. Tested in isolation with mocked router.
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
            LLMMessage(role="system", content=_load_system_prompt()),
            LLMMessage(role="user", content=user_prompt),
        ],
        agent=f"conflict_detect_{_prompt_version()}",
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    parsed = _parse_response(resp.content)
    return ConflictResult(
        decision=str(parsed["decision"]),
        confidence=float(parsed["confidence"]),
        reason=str(parsed.get("reason") or ""),
        sub_issues=tuple(
            SubIssue(title=str(s["title"]), summary=str(s.get("summary") or ""))
            for s in parsed["sub_issues"]
        ),
        cost_usd=resp.cost_usd,
        model=resp.model,
        raw=resp.raw,
    )


def _format_user_prompt(*, title: str, body: str, product_line: str, module: str) -> str:
    # Same trim policy as classify: long bodies waste tokens, rarely flip the verdict.
    snippet = (body or "")[:1500]
    return f"title={title!r}\nproduct_line={product_line!r}, module={module!r}\nbody={snippet!r}"


def _parse_response(content: str) -> dict[str, Any]:
    """Extract & validate the JSON envelope."""
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise ConflictDetectError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise ConflictDetectError(f"expected JSON object, got {type(data).__name__}")

    decision = data.get("decision")
    if decision not in _VALID_DECISIONS:
        raise ConflictDetectError(
            f"invalid decision {decision!r}; must be one of {sorted(_VALID_DECISIONS)}"
        )
    try:
        c = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise ConflictDetectError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= c <= 1.0:
        raise ConflictDetectError(f"confidence out of range: {c}")

    subs = data.get("sub_issues")
    if not isinstance(subs, list):
        raise ConflictDetectError(f"sub_issues must be a list, got {type(subs).__name__}")
    for s in subs:
        if not isinstance(s, dict) or not s.get("title"):
            raise ConflictDetectError(f"malformed sub_issue: {s!r}")
    if decision == "split" and len(subs) < 2:
        raise ConflictDetectError(f"decision=split needs >=2 sub_issues, got {len(subs)}")
    if decision == "no_split" and subs:
        # Tolerate (don't fail): model sometimes echoes the single issue back.
        data["sub_issues"] = []
    return data


def detect_ticket_conflict(ticket_id: int, db: Session | None = None) -> ConflictResult | None:
    """BackgroundTask body. Returns None on any failure (logged); never
    raises so failures don't crash the worker thread.

    Writes ONLY an agent_decisions audit row — tickets are not mutated
    (the split executor consumes 'split_ticket' proposals later).
    """
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None

    try:
        t = db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            logger.warning("conflict_detect_ticket_not_found", ticket_id=ticket_id)
            return None
        try:
            result = detect_conflict_payload(
                title=t.title,
                body=t.body,
                product_line_code=t.product_line_code,
                module=t.module,
            )
        except (ConflictDetectError, LLMRouterError) as e:
            logger.warning(
                "conflict_detect_failed",
                ticket_id=ticket_id,
                error=str(e),
            )
            return None

        decision_type = "split_ticket" if result.decision == "split" else "no_split"
        db.add(
            AgentDecision(
                decision_type=decision_type,
                subject_type="ticket",
                subject_id=t.id,
                proposal={
                    "decision": result.decision,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "sub_issues": [
                        {"title": s.title, "summary": s.summary} for s in result.sub_issues
                    ],
                    "model": result.model,
                    "cost_usd": result.cost_usd,
                    "prompt_version": _prompt_version(),
                },
            )
        )
        db.commit()
        logger.info(
            "conflict_detect_committed",
            ticket_id=ticket_id,
            short_code=t.short_code,
            decision=result.decision,
            confidence=result.confidence,
            sub_issue_count=len(result.sub_issues),
            cost_usd=result.cost_usd,
            model=result.model,
        )
        return result
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("conflict_detect_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
