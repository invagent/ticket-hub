"""Tests for conflict-detect agent (D3-D).

Pure-function parse/validation tests + DB-write tests with a fake provider.
Mirrors test_classify_agent.py structure.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider
from app.core.llm_router.router import LLMRouter, LLMRouterError
from app.models import AgentDecision, Source, Ticket
from app.services.agents.conflict_detect import (
    ConflictDetectError,
    detect_conflict_payload,
    detect_ticket_conflict,
)

SPLIT_JSON = (
    '{"decision": "split", "confidence": 0.88, "reason": "两个独立问题",'
    ' "sub_issues": ['
    '{"title": "红字确认单开票步骤咨询", "summary": "咨询正确操作流程"},'
    '{"title": "苍穹开票状态不同步", "summary": "税局已开票但系统显示未开票"}]}'
)
NO_SPLIT_JSON = (
    '{"decision": "no_split", "confidence": 0.95, "reason": "单一问题", "sub_issues": []}'
)


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, content: str, *, raises: Exception | None = None) -> None:
        self._content = content
        self._raises = raises

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        if self._raises is not None:
            raise self._raises
        return LLMResponse(
            content=self._content,
            provider=self.name,
            model="fake-model",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.001,
        )


def _router_with(content: str) -> LLMRouter:
    return LLMRouter([_FakeProvider(content)])


# ---- pure parsing / validation -------------------------------------------


def test_split_happy_path() -> None:
    res = detect_conflict_payload(
        title="1、开票步骤是什么 2、状态不同步",
        body="",
        product_line_code="cloud-fapiao",
        module=None,
        router=_router_with(SPLIT_JSON),
    )
    assert res.decision == "split"
    assert res.confidence == 0.88
    assert len(res.sub_issues) == 2
    assert res.sub_issues[0].title == "红字确认单开票步骤咨询"
    assert res.model == "fake-model"


def test_no_split_happy_path() -> None:
    res = detect_conflict_payload(
        title="全票池没有同步进来",
        body="排查日志为空",
        product_line_code="cloud-fapiao",
        module="全票池同步",
        router=_router_with(NO_SPLIT_JSON),
    )
    assert res.decision == "no_split"
    assert res.sub_issues == ()


def test_invalid_decision_raises() -> None:
    router = _router_with('{"decision": "maybe", "confidence": 0.5, "sub_issues": []}')
    with pytest.raises(ConflictDetectError, match="invalid decision"):
        detect_conflict_payload(
            title="x", body="y", product_line_code=None, module=None, router=router
        )


def test_split_with_one_sub_issue_raises() -> None:
    router = _router_with(
        '{"decision": "split", "confidence": 0.9, "sub_issues": [{"title": "只有一个"}]}'
    )
    with pytest.raises(ConflictDetectError, match=">=2 sub_issues"):
        detect_conflict_payload(
            title="x", body="y", product_line_code=None, module=None, router=router
        )


def test_no_split_with_echoed_sub_issue_tolerated() -> None:
    """模型偶尔在 no_split 时回显问题列表 — 容忍并清空，不报错。"""
    router = _router_with(
        '{"decision": "no_split", "confidence": 0.8, "sub_issues": [{"title": "回显"}]}'
    )
    res = detect_conflict_payload(
        title="x", body="y", product_line_code=None, module=None, router=router
    )
    assert res.decision == "no_split"
    assert res.sub_issues == ()


def test_malformed_sub_issue_raises() -> None:
    router = _router_with(
        '{"decision": "split", "confidence": 0.9, "sub_issues": [{"title": "ok"}, {"summary": "缺 title"}]}'
    )
    with pytest.raises(ConflictDetectError, match="malformed sub_issue"):
        detect_conflict_payload(
            title="x", body="y", product_line_code=None, module=None, router=router
        )


def test_non_json_output_raises() -> None:
    router = _router_with("我觉得应该拆分")
    with pytest.raises(ConflictDetectError, match="non-JSON"):
        detect_conflict_payload(
            title="x", body="y", product_line_code=None, module=None, router=router
        )


def test_confidence_out_of_range_raises() -> None:
    router = _router_with('{"decision": "no_split", "confidence": 7, "sub_issues": []}')
    with pytest.raises(ConflictDetectError, match="out of range"):
        detect_conflict_payload(
            title="x", body="y", product_line_code=None, module=None, router=router
        )


# ---- DB write path ---------------------------------------------------------


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _make_ticket(db: Session, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-CFD-1",
        "source_code": "ksm",
        "source_ticket_id": "cfd-1",
        "type": "Raw",
        "status": "received",
        "title": "1、开票步骤咨询 2、状态不同步",
        "body": "详见标题",
        "product_line_code": None,
        "module": None,
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _patch_router(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    from app.services.agents import conflict_detect as mod

    monkeypatch.setattr(
        mod.LLMRouter,
        "from_settings",
        classmethod(lambda _cls, **_kw: _router_with(content)),
    )


def test_detect_writes_split_decision(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router(monkeypatch, SPLIT_JSON)
    t = _make_ticket(world)
    res = detect_ticket_conflict(t.id, db=world)
    assert res is not None
    assert res.decision == "split"

    rows = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=t.id).all()
    assert len(rows) == 1
    d = rows[0]
    assert d.decision_type == "split_ticket"
    assert d.status == "executed"
    assert d.proposal["decision"] == "split"
    assert len(d.proposal["sub_issues"]) == 2
    assert d.proposal["model"] == "fake-model"
    assert d.proposal["skill"] == "conflict_detect"
    # D3-D 仅审计建议，不改工单
    world.refresh(t)
    assert t.type == "Raw"


def test_detect_writes_no_split_decision(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router(monkeypatch, NO_SPLIT_JSON)
    t = _make_ticket(world, short_code="TKT-CFD-2", source_ticket_id="cfd-2")
    res = detect_ticket_conflict(t.id, db=world)
    assert res is not None
    assert res.decision == "no_split"

    d = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=t.id).one()
    assert d.decision_type == "no_split"
    assert d.proposal["sub_issues"] == []


def test_detect_swallows_router_error(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.agents import conflict_detect as mod

    def _boom(_cls: type, **_kw: object) -> LLMRouter:
        raise LLMRouterError("all providers down", attempts=[])

    monkeypatch.setattr(mod.LLMRouter, "from_settings", classmethod(_boom))
    t = _make_ticket(world, short_code="TKT-CFD-3", source_ticket_id="cfd-3")
    assert detect_ticket_conflict(t.id, db=world) is None
    assert world.query(AgentDecision).filter_by(subject_id=t.id).count() == 0


def test_detect_ticket_not_found_silent(world: Session) -> None:
    assert detect_ticket_conflict(999999, db=world) is None


def test_detect_invalid_llm_output_silent(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router(monkeypatch, "not json at all")
    t = _make_ticket(world, short_code="TKT-CFD-4", source_ticket_id="cfd-4")
    assert detect_ticket_conflict(t.id, db=world) is None
    assert world.query(AgentDecision).filter_by(subject_id=t.id).count() == 0
