"""AI 客服查询服务（对外接口 + 内部自动答复共享）.

把「拼问题 → ai_cs.replay 生成答复（带超时重试）→ answer-router 判 D/C/transfer」
这段纯生成逻辑抽出来，不写库、不级联、不关单。两个消费方：

  - services/agents/operation_answer.auto_answer_operation（内部自动答复，判 D 才回写）
  - api/ai_cs_query（对外 HTTP 接口，直接返回答复 + 判定 + 建议）

replay 网络/超时错误即时重试最多 _REPLAY_MAX_ATTEMPTS 次；业务错误不重试。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from adapters.ai_cs import AiCsBusinessError, AiCsClient, AiCsError, AiCsNetworkError
from app.config import Settings, get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.services.knowledge_feedback.service import KnowledgeFeedbackDisabledError, build_client
from app.services.skills.prompt_store import load_prompt

logger = get_logger(__name__)

_VALID_BRANCHES = frozenset({"C", "D", "transfer"})
# replay 网络/超时错误即时重试次数（偶发抖动兜底；业务错误不重试）
_REPLAY_MAX_ATTEMPTS = 3
_DEFAULT_SKILL = "customer-service"


@dataclass(slots=True, frozen=True)
class AnswerRoute:
    branch: str  # "C" | "D" | "transfer"
    supply_note: str = ""


@dataclass(slots=True, frozen=True)
class AnswerResult:
    answer: str
    branch: str  # D=可直接答复 / C=信息不足需补料 / transfer=建议转人工
    supply_note: str  # C 时的补料建议文案，其余为空


def build_question(*, title: str, content: str, product_category: str = "") -> str:
    """拼客户问题：{产品分类}：{标题} {内容}。与自动答复口径一致（去空段/前缀）。"""
    body = " ".join(p for p in (title.strip(), content.strip()) if p).strip()
    q = f"{product_category.strip()}：{body}" if product_category.strip() else body
    return q.lstrip("-：").strip() or body


def resolve_default_skill(settings: Settings) -> str:
    """取受管理列表第一个 skill 作默认（AI 客服服务端要求 skill 在受管理列表内）。"""
    return (
        next((s.strip() for s in settings.ai_cs_managed_skills.split(",") if s.strip()), None)
        or _DEFAULT_SKILL
    )


def replay_with_retry(
    client: AiCsClient, *, question: str, skill: str | None, label: str = ""
) -> str:
    """调 ai_cs.replay 生成答复；网络/超时错误最多重试 _REPLAY_MAX_ATTEMPTS 次。

    业务错误（skill 非法/鉴权）不重试直接抛。耗尽后抛最后一次的 AiCsError。
    """
    last_err: AiCsError | None = None
    for attempt in range(1, _REPLAY_MAX_ATTEMPTS + 1):
        try:
            result = client.replay(question=question, skill=skill, use_latest_knowledge=True)
            return str(result.answer)
        except AiCsNetworkError as e:
            last_err = e
            logger.warning(
                "ai_cs_replay_timeout",
                label=label,
                attempt=attempt,
                max_attempts=_REPLAY_MAX_ATTEMPTS,
                error=str(e),
            )
            continue
        except AiCsError as e:
            logger.warning("ai_cs_replay_failed", label=label, error=str(e))
            raise
    logger.warning(
        "ai_cs_replay_exhausted", label=label, attempts=_REPLAY_MAX_ATTEMPTS, error=str(last_err)
    )
    assert last_err is not None
    raise last_err


def route_answer(question: str, answer: str, *, router: LLMRouter | None = None) -> AnswerRoute:
    """answer-router LLM 判 C/D/transfer。异常/非法一律兜底 transfer。"""
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


class AiCsQueryDisabledError(Exception):
    """AI 客服未启用/未配置 — 调用方映射 HTTP 503。"""


def answer_question(
    *,
    title: str,
    content: str,
    product_category: str = "",
    skill: str | None = None,
    settings: Settings | None = None,
) -> AnswerResult:
    """对外查询入口：生成 AI 客服答复 + answer-router 判定。不写库、不关单。

    skill 不传则用受管理列表第一个（默认 customer-service）。
    raises AiCsQueryDisabledError（未配置）/ AiCsError（replay 耗尽或业务错误）。
    """
    settings = settings or get_settings()
    try:
        client = build_client(settings)
    except KnowledgeFeedbackDisabledError as e:
        raise AiCsQueryDisabledError(str(e)) from e

    question = build_question(title=title, content=content, product_category=product_category)
    resolved_skill = (skill or "").strip() or resolve_default_skill(settings)
    try:
        answer = replay_with_retry(
            client, question=question, skill=resolved_skill, label="ai_cs_query"
        )
    finally:
        client.close()

    route = route_answer(question, answer)
    return AnswerResult(answer=answer, branch=route.branch, supply_note=route.supply_note)


__all__ = [
    "AiCsBusinessError",
    "AiCsError",
    "AiCsQueryDisabledError",
    "AnswerResult",
    "AnswerRoute",
    "answer_question",
    "build_question",
    "replay_with_retry",
    "resolve_default_skill",
    "route_answer",
]
