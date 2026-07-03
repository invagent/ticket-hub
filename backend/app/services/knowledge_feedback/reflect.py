"""Escalation 反思推断 agent — 反思诊断工作台的「AI 反思推断」面板.

Supervisor-triggered (never auto): given the escalation context (golden triple
+ conversation + cited knowledge) and, ideally, the supervisor-verified correct
answer, the LLM walks a fixed 3-step audit and infers the root cause:

    skill      引用了对的知识但答复没用好 → 修订 skill 提示词
    knowledge  引用的知识本身有误/过期    → 修订知识条目
    retrieval  没检索到相关知识           → 补充知识条目

The result is cached on ticket.source_payload['ai_cs']['reflection'] so
re-opening the workbench doesn't re-run the LLM; a new run overwrites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.core.llm_router import LLMMessage, LLMRouter
from app.core.logging import get_logger

logger = get_logger(__name__)

_VALID_CAUSES = frozenset({"skill", "knowledge", "retrieval"})
_MAX_CONV_TURNS = 20
_MAX_SNIPPET = 400


class ReflectError(Exception):
    """LLM output couldn't be parsed/validated."""


@dataclass(slots=True, frozen=True)
class ReflectResult:
    steps: list[dict[str, Any]]
    cause: str
    confidence: float
    reason: str
    suggested_revision: str | None
    cost_usd: float
    model: str

    def as_payload(self) -> dict[str, Any]:
        """Shape persisted under source_payload['ai_cs']['reflection']."""
        return {
            "steps": self.steps,
            "cause": self.cause,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested_revision": self.suggested_revision,
            "model": self.model,
            "at": datetime.now(UTC).isoformat(),
        }


def _load_system_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    version = get_settings().escalation_reflect_prompt_version
    return load_prompt(f"escalation_reflect_{version}")


def _build_user_prompt(
    *,
    question: str,
    ai_answer: str,
    dissatisfaction: str,
    conversation: list[dict[str, Any]],
    cited_knowledge: list[dict[str, Any]],
    correct_answer: str | None,
) -> str:
    lines = [
        f"客户原始问题：{question}",
        f"AI 客服答复：{ai_answer or '（无）'}",
        f"客户不满反馈：{dissatisfaction or '（无）'}",
    ]
    if conversation:
        lines.append("完整会话：")
        for m in conversation[:_MAX_CONV_TURNS]:
            who = "客户" if m.get("role") == "user" else "AI"
            lines.append(f"  [{who}] {m.get('text', '')}")
    if cited_knowledge:
        lines.append("AI 答复引用的知识：")
        for c in cited_knowledge:
            score = c.get("score")
            score_txt = f"（相似度 {float(score):.2f}）" if isinstance(score, int | float) else ""
            snippet = str(c.get("snippet") or "")[:_MAX_SNIPPET]
            lines.append(f"  - [{c.get('type', '?')}] {c.get('title', '')}{score_txt}：{snippet}")
    else:
        lines.append("AI 答复引用的知识：（无 — 本次答复未引用任何知识）")
    lines.append(
        f"人工核对的正确答案：{correct_answer}"
        if correct_answer
        else "人工核对的正确答案：（未提供 — 基于会话与引用推断，注明置信有限）"
    )
    return "\n".join(lines)


def run_reflect(
    *,
    question: str,
    ai_answer: str,
    dissatisfaction: str,
    conversation: list[dict[str, Any]],
    cited_knowledge: list[dict[str, Any]],
    correct_answer: str | None = None,
    router: LLMRouter | None = None,
) -> ReflectResult:
    router = router or LLMRouter.from_settings()
    version = get_settings().escalation_reflect_prompt_version
    resp = router.complete(
        [
            LLMMessage(role="system", content=_load_system_prompt()),
            LLMMessage(
                role="user",
                content=_build_user_prompt(
                    question=question,
                    ai_answer=ai_answer,
                    dissatisfaction=dissatisfaction,
                    conversation=conversation,
                    cited_knowledge=cited_knowledge,
                    correct_answer=correct_answer,
                ),
            ),
        ],
        agent=f"escalation_reflect_{version}",
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = _parse(resp.content)
    return ReflectResult(
        steps=parsed["steps"],
        cause=parsed["cause"],
        confidence=parsed["confidence"],
        reason=parsed["reason"],
        suggested_revision=parsed["suggested_revision"],
        cost_usd=resp.cost_usd,
        model=resp.model,
    )


def _parse(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise ReflectError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise ReflectError(f"expected JSON object, got {type(data).__name__}")

    cause = data.get("cause")
    if cause not in _VALID_CAUSES:
        raise ReflectError(f"invalid cause {cause!r}; must be one of {sorted(_VALID_CAUSES)}")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ReflectError("missing/empty steps")
    steps: list[dict[str, Any]] = []
    for s in raw_steps:
        if not isinstance(s, dict) or not s.get("title"):
            raise ReflectError(f"bad step: {s!r}")
        good = s.get("good")
        steps.append(
            {
                "title": str(s["title"]),
                "detail": str(s.get("detail") or ""),
                "verdict": str(s["verdict"]) if s.get("verdict") else None,
                "good": bool(good) if isinstance(good, bool) else None,
            }
        )

    try:
        confidence = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise ReflectError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= confidence <= 1.0:
        raise ReflectError(f"confidence out of range: {confidence}")

    suggested = data.get("suggested_revision")
    return {
        "steps": steps,
        "cause": cause,
        "confidence": confidence,
        "reason": str(data.get("reason") or ""),
        "suggested_revision": str(suggested) if suggested else None,
    }
