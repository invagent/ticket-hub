"""对外 AI 客服查询接口 — 复用现有 AI 客服能力直接返回答复 + 建议.

  POST /api/ai-cs/answer?access_token=<webhook_token>
    body: {"title": ..., "content": ..., "product_category": ..., "skill": ...}
    resp: {"answer": ..., "branch": "D|C|transfer", "supply_note": ..., "skill": ...}

鉴权复用 webhook_access_token（与 KSM/智齿 webhook 一致）。不写库、不关单，
纯生成：拼问题 → ai_cs.replay（带超时重试）→ answer-router 判 D/C/transfer。
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from adapters.ai_cs import AiCsError
from app.config import get_settings
from app.core.logging import get_logger
from app.services.ai_cs.query import (
    AiCsQueryDisabledError,
    answer_question,
    resolve_default_skill,
)

router = APIRouter()
logger = get_logger(__name__)


class AnswerRequest(BaseModel):
    title: str = Field(default="", description="工单标题")
    content: str = Field(default="", description="工单内容/问题描述")
    product_category: str = Field(default="", description="产品分类（如 星瀚-收票），可空")
    skill: str | None = Field(
        default=None, description="AI 客服 skill，不传用默认 customer-service"
    )


class AnswerResponse(BaseModel):
    answer: str
    branch: str  # D=可直接答复 / C=信息不足需补料 / transfer=建议转人工
    supply_note: str  # C 时的补料建议文案，其余为空
    skill: str


def _verify_token(provided: str | None) -> None:
    expected = get_settings().webhook_access_token
    if not expected:
        raise HTTPException(status_code=503, detail="access_token auth not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid access_token")


@router.post("/answer", response_model=AnswerResponse)
def answer(body: AnswerRequest, access_token: str = Query(...)) -> AnswerResponse:
    """生成 AI 客服答复 + 判定。title/content 至少一个非空。"""
    _verify_token(access_token)
    if not (body.title.strip() or body.content.strip()):
        raise HTTPException(status_code=400, detail="title 和 content 不能都为空")

    settings = get_settings()
    used_skill = (body.skill or "").strip() or resolve_default_skill(settings)
    try:
        result = answer_question(
            title=body.title,
            content=body.content,
            product_category=body.product_category,
            skill=used_skill,
            settings=settings,
        )
    except AiCsQueryDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except AiCsError as e:
        # replay 耗尽重试或业务错误（skill 非法等）
        raise HTTPException(status_code=502, detail=f"AI 客服调用失败：{e}") from e

    logger.info("ai_cs_query_answered", branch=result.branch, skill=used_skill)
    return AnswerResponse(
        answer=result.answer,
        branch=result.branch,
        supply_note=result.supply_note,
        skill=used_skill,
    )
