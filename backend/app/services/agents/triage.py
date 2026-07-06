"""Triage agent（ADR-0016 P2b）— classify + conflict_detect 合一.

一次 LLM 出 {type, confidence, reason, is_mixed, sub_problems[]}，替代原来的
两次调用（classify → conflict_detect）。类型定义走共享 type_taxonomy 片段。

持久化（run_ticket_triage）：
  - tickets.predicted_type / predicted_confidence / classified_at（同 classify）
  - agent_decisions 审计：classify_type 一行 + (混合时) split_ticket 一行
    （沿用既有 decision_type，split 执行器与工作台队列无需改）

链路（run_post_ingest_agents，P2c）在此之上做 split 前置 + 按类型分流。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket

logger = get_logger(__name__)

_VALID_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task", "Complaint"})
_SKILL_NAME = "triage"


class TriageError(Exception):
    """LLM output couldn't be parsed/validated."""


@dataclass(slots=True, frozen=True)
class SubProblem:
    title: str
    summary: str
    type: str


@dataclass(slots=True, frozen=True)
class TriageResult:
    type: str
    confidence: float
    reason: str
    is_mixed: bool
    sub_problems: tuple[SubProblem, ...]
    cost_usd: float
    model: str
    raw: dict[str, Any]


def _assemble(body: str) -> str:
    from app.services.skills.prompt_store import assemble_prompt

    return assemble_prompt(body)


def _load_system_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return _assemble(load_prompt(_SKILL_NAME))


def _format_user_prompt(*, title: str, body: str, product_line: str, module: str) -> str:
    snippet = (body or "")[:1500]
    return f"title={title!r}\nproduct_line={product_line!r}, module={module!r}\nbody={snippet!r}"


def triage_payload(
    *,
    title: str | None,
    body: str | None,
    product_line_code: str | None,
    module: str | None,
    router: LLMRouter | None = None,
    system_prompt_override: str | None = None,
) -> TriageResult:
    """Pure function: fields → TriageResult. Draft 验证器可注入 override。"""
    router = router or LLMRouter.from_settings()
    system = (
        _assemble(system_prompt_override)
        if system_prompt_override is not None
        else _load_system_prompt()
    )
    resp = router.complete(
        [
            LLMMessage(role="system", content=system),
            LLMMessage(
                role="user",
                content=_format_user_prompt(
                    title=title or "",
                    body=body or "",
                    product_line=product_line_code or "",
                    module=module or "",
                ),
            ),
        ],
        agent=_SKILL_NAME if system_prompt_override is None else f"{_SKILL_NAME}:draft",
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = _parse(resp.content)
    return TriageResult(
        type=parsed["type"],
        confidence=parsed["confidence"],
        reason=parsed["reason"],
        is_mixed=parsed["is_mixed"],
        sub_problems=tuple(
            SubProblem(title=s["title"], summary=s["summary"], type=s["type"])
            for s in parsed["sub_problems"]
        ),
        cost_usd=resp.cost_usd,
        model=resp.model,
        raw=resp.raw,
    )


def _parse(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise TriageError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise TriageError(f"expected JSON object, got {type(data).__name__}")

    t = data.get("type")
    if t not in _VALID_TYPES:
        raise TriageError(f"invalid type {t!r}; must be one of {sorted(_VALID_TYPES)}")
    try:
        conf = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise TriageError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= conf <= 1.0:
        raise TriageError(f"confidence out of range: {conf}")

    is_mixed = bool(data.get("is_mixed"))
    raw_subs = data.get("sub_problems") or []
    if not isinstance(raw_subs, list):
        raise TriageError(f"sub_problems must be a list, got {type(raw_subs).__name__}")
    subs: list[dict[str, str]] = []
    for s in raw_subs:
        if not isinstance(s, dict) or not s.get("title"):
            raise TriageError(f"malformed sub_problem: {s!r}")
        st = s.get("type")
        if st not in _VALID_TYPES:
            raise TriageError(f"sub_problem type {st!r} invalid")
        subs.append(
            {"title": str(s["title"]), "summary": str(s.get("summary") or ""), "type": str(st)}
        )
    if is_mixed and len(subs) < 2:
        raise TriageError(f"is_mixed=true needs >=2 sub_problems, got {len(subs)}")
    if not is_mixed:
        subs = []  # tolerate model echoing a single sub-problem back
    return {
        "type": t,
        "confidence": round(conf, 2),
        "reason": str(data.get("reason") or ""),
        "is_mixed": is_mixed,
        "sub_problems": subs,
    }


def run_ticket_triage(ticket_id: int, db: Session | None = None) -> TriageResult | None:
    """BG task body. 写 predicted_* + classify_type 审计（+ 混合时 split_ticket
    审计）。任何失败返回 None（记日志不抛），不阻塞 worker。"""
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None
    try:
        t = db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            logger.warning("triage_ticket_not_found", ticket_id=ticket_id)
            return None
        try:
            result = triage_payload(
                title=t.title,
                body=t.body,
                product_line_code=t.product_line_code,
                module=t.module,
            )
        except (TriageError, LLMRouterError) as e:
            logger.warning("triage_failed", ticket_id=ticket_id, error=str(e))
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
                    "skill": _SKILL_NAME,
                },
            )
        )
        if result.is_mixed:
            # split_ticket 审计行——沿用 conflict_detect 的 proposal 形状，
            # split 执行器与工作台「待拆分」队列直接消费（sub_issues 字段名保持）。
            db.add(
                AgentDecision(
                    decision_type="split_ticket",
                    subject_type="ticket",
                    subject_id=t.id,
                    proposal={
                        "decision": "split",
                        "confidence": result.confidence,
                        "reason": result.reason,
                        "sub_issues": [
                            {"title": s.title, "summary": s.summary, "sub_type": s.type}
                            for s in result.sub_problems
                        ],
                        "model": result.model,
                        "cost_usd": result.cost_usd,
                        "skill": _SKILL_NAME,
                    },
                )
            )
        db.commit()
        logger.info(
            "triage_committed",
            ticket_id=ticket_id,
            short_code=t.short_code,
            predicted_type=result.type,
            confidence=result.confidence,
            is_mixed=result.is_mixed,
            sub_count=len(result.sub_problems),
            cost_usd=result.cost_usd,
            model=result.model,
        )
        return result
    except Exception:
        if own_session:
            db.rollback()
        logger.exception("triage_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
