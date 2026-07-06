"""Triage agent tests（ADR-0016 P2b）— 合并分类+混合判定的解析与持久化."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider
from app.core.llm_router.router import LLMRouter
from app.models import AgentDecision, Source, Ticket
from app.services.agents.triage import TriageError, run_ticket_triage, triage_payload


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        return LLMResponse(
            content=self._content,
            provider=self.name,
            model="fake-model",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.002,
        )


def _router(content: str) -> LLMRouter:
    return LLMRouter([_FakeProvider(content)])


_SINGLE = '{"type":"Bug_fix","confidence":0.95,"reason":"报错","is_mixed":false,"sub_problems":[]}'
_MIXED = (
    '{"type":"Bug_fix","confidence":0.8,"reason":"登录故障影响最大","is_mixed":true,'
    '"sub_problems":['
    '{"title":"登录失败","summary":"密码错误","type":"Bug_fix"},'
    '{"title":"补开发票","summary":"5月漏票","type":"Operation"}]}'
)


def test_triage_single_problem() -> None:
    r = triage_payload(
        title="报错", body="500", product_line_code="p", module="m", router=_router(_SINGLE)
    )
    assert r.type == "Bug_fix" and r.is_mixed is False and r.sub_problems == ()


def test_triage_mixed() -> None:
    r = triage_payload(
        title="x", body="y", product_line_code="p", module="m", router=_router(_MIXED)
    )
    assert r.is_mixed is True
    assert len(r.sub_problems) == 2
    assert r.sub_problems[1].type == "Operation"


def test_triage_complaint() -> None:
    c = '{"type":"Complaint","confidence":0.9,"reason":"投诉","is_mixed":false,"sub_problems":[]}'
    r = triage_payload(
        title="投诉", body="太慢", product_line_code="p", module="m", router=_router(c)
    )
    assert r.type == "Complaint"


def test_triage_mixed_needs_two_subs() -> None:
    bad = '{"type":"Bug_fix","confidence":0.8,"reason":"r","is_mixed":true,"sub_problems":[{"title":"a","summary":"s","type":"Bug_fix"}]}'
    with pytest.raises(TriageError, match=">=2 sub_problems"):
        triage_payload(title="x", body="y", product_line_code="p", module="m", router=_router(bad))


def test_triage_invalid_type() -> None:
    bad = '{"type":"Nope","confidence":0.8,"reason":"r","is_mixed":false,"sub_problems":[]}'
    with pytest.raises(TriageError, match="invalid type"):
        triage_payload(title="x", body="y", product_line_code="p", module="m", router=_router(bad))


def test_triage_bad_sub_type() -> None:
    bad = '{"type":"Bug_fix","confidence":0.8,"reason":"r","is_mixed":true,"sub_problems":[{"title":"a","summary":"s","type":"X"},{"title":"b","summary":"s","type":"Operation"}]}'
    with pytest.raises(TriageError, match="sub_problem type"):
        triage_payload(title="x", body="y", product_line_code="p", module="m", router=_router(bad))


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _mk(db: Session, **ov) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-TRI-1",
        "source_code": "ksm",
        "source_ticket_id": "tri-1",
        "type": "Raw",
        "status": "received",
        "title": "标题",
        "body": "描述",
    }
    base.update(ov)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_run_triage_single_writes_classify_only(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.agents import triage as mod

    monkeypatch.setattr(mod.LLMRouter, "from_settings", classmethod(lambda _c: _router(_SINGLE)))
    t = _mk(world)
    res = run_ticket_triage(t.id, db=world)
    assert res is not None and res.type == "Bug_fix"
    world.refresh(t)
    assert t.predicted_type == "Bug_fix"
    decisions = world.query(AgentDecision).filter_by(subject_id=t.id).all()
    kinds = {d.decision_type for d in decisions}
    assert kinds == {"classify_type"}  # 非混合：无 split_ticket


def test_run_triage_mixed_writes_split(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.agents import triage as mod

    monkeypatch.setattr(mod.LLMRouter, "from_settings", classmethod(lambda _c: _router(_MIXED)))
    t = _mk(world, short_code="TKT-TRI-2", source_ticket_id="tri-2")
    run_ticket_triage(t.id, db=world)
    decisions = world.query(AgentDecision).filter_by(subject_id=t.id).all()
    kinds = {d.decision_type for d in decisions}
    assert kinds == {"classify_type", "split_ticket"}
    split = next(d for d in decisions if d.decision_type == "split_ticket")
    assert len(split.proposal["sub_issues"]) == 2
    assert split.proposal["skill"] == "triage"
