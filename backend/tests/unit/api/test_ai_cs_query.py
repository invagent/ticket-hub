"""对外 AI 客服查询接口 /api/ai-cs/answer 单测。"""

from __future__ import annotations

from unittest.mock import patch

from adapters.ai_cs import AiCsError
from app.services.ai_cs.query import (
    AiCsQueryDisabledError,
    AnswerResult,
    build_question,
    resolve_default_skill,
)

_URL = "/api/ai-cs/answer"


def test_answer_success(app_client) -> None:  # type: ignore[no-untyped-def]
    fake = AnswerResult(answer="配置步骤如下…", branch="D", supply_note="")
    with patch("app.api.ai_cs_query.answer_question", return_value=fake) as m:
        resp = app_client.post(
            f"{_URL}?access_token=test-token",
            json={
                "title": "如何配置软证书",
                "content": "勾选认证提示",
                "product_category": "星瀚-收票",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "配置步骤如下…"
    assert body["branch"] == "D"
    assert body["supply_note"] == ""
    assert body["skill"] == "customer-service"  # 默认
    # 调用方未传 skill → 服务层收到默认 skill
    assert m.call_args.kwargs["skill"] == "customer-service"


def test_answer_c_branch_returns_supply_note(app_client) -> None:  # type: ignore[no-untyped-def]
    fake = AnswerResult(answer="请问用哪款产品？", branch="C", supply_note="请提供产品版本")
    with patch("app.api.ai_cs_query.answer_question", return_value=fake):
        resp = app_client.post(f"{_URL}?access_token=test-token", json={"content": "怎么开票"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch"] == "C"
    assert body["supply_note"] == "请提供产品版本"


def test_answer_custom_skill_passed_through(app_client) -> None:  # type: ignore[no-untyped-def]
    fake = AnswerResult(answer="a", branch="D", supply_note="")
    with patch("app.api.ai_cs_query.answer_question", return_value=fake) as m:
        resp = app_client.post(
            f"{_URL}?access_token=test-token",
            json={"content": "x", "skill": "customer-service-feishu"},
        )
    assert resp.status_code == 200
    assert resp.json()["skill"] == "customer-service-feishu"
    assert m.call_args.kwargs["skill"] == "customer-service-feishu"


def test_answer_bad_token(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(f"{_URL}?access_token=wrong", json={"content": "x"})
    assert resp.status_code == 401


def test_answer_empty_title_and_content(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(f"{_URL}?access_token=test-token", json={"title": "  ", "content": ""})
    assert resp.status_code == 400


def test_answer_disabled_returns_503(app_client) -> None:  # type: ignore[no-untyped-def]
    with patch(
        "app.api.ai_cs_query.answer_question",
        side_effect=AiCsQueryDisabledError("AI 客服未配置"),
    ):
        resp = app_client.post(f"{_URL}?access_token=test-token", json={"content": "x"})
    assert resp.status_code == 503


def test_answer_ai_cs_error_returns_502(app_client) -> None:  # type: ignore[no-untyped-def]
    with patch("app.api.ai_cs_query.answer_question", side_effect=AiCsError("replay timed out")):
        resp = app_client.post(f"{_URL}?access_token=test-token", json={"content": "x"})
    assert resp.status_code == 502


# ---- 纯函数 ----


def test_build_question_with_category() -> None:
    assert build_question(title="标题", content="内容", product_category="星瀚-收票") == (
        "星瀚-收票：标题 内容"
    )


def test_build_question_no_category() -> None:
    assert build_question(title="", content="只有内容") == "只有内容"


class _S:
    ai_cs_managed_skills = "customer-service,customer-service-feishu"


def test_resolve_default_skill_takes_first() -> None:
    assert resolve_default_skill(_S()) == "customer-service"  # type: ignore[arg-type]
