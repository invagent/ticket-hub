"""Operation 答复准确率打分器（独立 LLM 调用，仿 answer_router）.

在 answer_router 判 D 之后对答复打分（0-100），依据知识库+FAQ 综合判断。
异常/非法 JSON 一律兜底 accuracy=0（安全侧：打分失败视作低置信转主管）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.services.skills.prompt_store import load_prompt

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class AccuracyScore:
    accuracy: int  # 0-100
    reason: str = ""


def score_answer_accuracy(
    question: str,
    answer: str,
    cited_knowledge: list[dict],
    *,
    router: LLMRouter | None = None,
) -> AccuracyScore:
    """LLM 打分。异常/非法一律兜底 accuracy=0（低置信留主管）。"""
    try:
        prompt = load_prompt("answer_accuracy")
        router = router or LLMRouter.from_settings()
        cited_text = json.dumps(cited_knowledge, ensure_ascii=False)
        resp = router.complete(
            [
                LLMMessage(role="system", content=prompt),
                LLMMessage(
                    role="user",
                    content=f"客户问题：{question}\n\nAI 答复：{answer}\n\n引用知识：{cited_text}",
                ),
                LLMMessage(role="user", content="只输出 JSON。"),
            ],
            agent="answer_accuracy",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.content)
        if not isinstance(data, dict):
            return AccuracyScore(accuracy=0, reason="打分返回非对象JSON，兜底转主管")
        raw = int(data.get("accuracy"))
        accuracy = max(0, min(100, raw))  # clamp
        return AccuracyScore(accuracy=accuracy, reason=str(data.get("reason") or ""))
    except (LLMRouterError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning("answer_accuracy_scoring_failed", error=str(e))
        return AccuracyScore(accuracy=0, reason="打分失败，兜底转主管")
