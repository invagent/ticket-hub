"""escalation_reflect agent tests — prompt assembly + LLM output parsing."""

from __future__ import annotations

import pytest

from app.services.knowledge_feedback.reflect import ReflectError, _build_user_prompt, _parse


def _good_output(**ov) -> dict:  # type: ignore[type-arg, no-untyped-def]
    base = {
        "steps": [
            {
                "title": "客户真正的卡点是什么",
                "detail": "实名已完成仍超时",
                "verdict": None,
                "good": None,
            },
            {"title": "知识库是否覆盖正解", "detail": "F201 命中", "verdict": "命中", "good": True},
            {
                "title": "AI 是否用好了命中的知识",
                "detail": "未采用 F201",
                "verdict": "未采用",
                "good": False,
            },
        ],
        "cause": "skill",
        "confidence": 0.86,
        "reason": "skill 把开票问题默认导向实名认证",
        "suggested_revision": "改第 2 条规则：先按报错现象分诊",
    }
    base.update(ov)
    return base


def test_parse_happy_path() -> None:
    import json

    # 旧单值 cause 兼容：包装成单元素 causes 集合
    r = _parse(json.dumps(_good_output()))
    assert r["causes"] == ["skill"]
    assert r["confidence"] == 0.86
    assert len(r["steps"]) == 3
    assert r["steps"][1]["verdict"] == "命中" and r["steps"][1]["good"] is True
    assert r["steps"][0]["good"] is None
    assert r["suggested_revision"].startswith("改第 2 条")


def test_parse_null_suggestion() -> None:
    import json

    r = _parse(json.dumps(_good_output(cause="retrieval", suggested_revision=None)))
    assert r["suggested_revision"] is None


def test_parse_invalid_cause_raises() -> None:
    import json

    with pytest.raises(ReflectError, match="invalid cause"):
        _parse(json.dumps(_good_output(cause="both")))


def test_parse_multi_causes() -> None:
    """ADR-0016 决策 6：多病因集合，主次排序 + 去重保序。"""
    import json

    out = _good_output(causes=["knowledge", "skill", "knowledge"])
    del out["cause"]
    r = _parse(json.dumps(out))
    assert r["causes"] == ["knowledge", "skill"]


def test_parse_multi_causes_invalid_member_raises() -> None:
    import json

    out = _good_output(causes=["skill", "nope"])
    del out["cause"]
    with pytest.raises(ReflectError, match="invalid cause"):
        _parse(json.dumps(out))


def test_parse_empty_causes_raises() -> None:
    import json

    out = _good_output(causes=[])
    del out["cause"]
    with pytest.raises(ReflectError, match="missing/empty causes"):
        _parse(json.dumps(out))


def test_parse_missing_steps_raises() -> None:
    import json

    with pytest.raises(ReflectError, match="steps"):
        _parse(json.dumps(_good_output(steps=[])))


def test_parse_confidence_out_of_range_raises() -> None:
    import json

    with pytest.raises(ReflectError, match="confidence"):
        _parse(json.dumps(_good_output(confidence=1.5)))


def test_parse_non_json_raises() -> None:
    with pytest.raises(ReflectError, match="non-JSON"):
        _parse("对不起，我无法输出 JSON")


def test_user_prompt_includes_all_sections() -> None:
    p = _build_user_prompt(
        question="开票超时",
        ai_answer="请实名认证",
        dissatisfaction="做了还是超时",
        conversation=[
            {"role": "user", "text": "开不了票"},
            {"role": "assistant", "text": "请认证"},
        ],
        cited_knowledge=[
            {"type": "faq", "title": "超时排查", "score": 0.71, "snippet": "通道拥堵"}
        ],
        correct_answer="实为通道拥堵",
    )
    assert "客户原始问题：开票超时" in p
    assert "[客户] 开不了票" in p and "[AI] 请认证" in p
    assert "[faq] 超时排查（相似度 0.71）：通道拥堵" in p
    assert "人工核对的正确答案：实为通道拥堵" in p


def test_user_prompt_degrades_without_extras() -> None:
    p = _build_user_prompt(
        question="q",
        ai_answer="",
        dissatisfaction="",
        conversation=[],
        cited_knowledge=[],
        correct_answer=None,
    )
    assert "未引用任何知识" in p
    assert "未提供" in p
    assert "完整会话" not in p
