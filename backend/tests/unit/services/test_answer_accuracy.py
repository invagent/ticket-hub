from __future__ import annotations

from types import SimpleNamespace

from app.services.agents.answer_accuracy import AccuracyScore, score_answer_accuracy


class _FakeRouter:
    def __init__(self, content: str, raise_err: bool = False) -> None:
        self._content = content
        self._raise = raise_err

    def complete(self, messages: object, **kw: object) -> object:
        if self._raise:
            from app.core.llm_router import LLMRouterError

            raise LLMRouterError("boom")
        return SimpleNamespace(content=self._content, cost_usd=0.0, model="fake")


def test_score_parses_accuracy_and_reason() -> None:
    r = score_answer_accuracy(
        "开票失败怎么办",
        "请在发票管理页重新发起。",
        [{"title": "开票指引", "content": "在发票管理页重新发起开票"}],
        router=_FakeRouter('{"accuracy": 95, "reason": "与知识库一致"}'),
    )
    assert isinstance(r, AccuracyScore)
    assert r.accuracy == 95
    assert "一致" in r.reason


def test_score_llm_error_defaults_zero() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter("", raise_err=True)
    )
    assert r.accuracy == 0


def test_score_invalid_json_defaults_zero() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter("not json")
    )
    assert r.accuracy == 0


def test_score_out_of_range_clamped() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter('{"accuracy": 150, "reason": "r"}')
    )
    assert 0 <= r.accuracy <= 100
