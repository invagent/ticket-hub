"""escalation_classify agent tests — golden-triple parse + DB write."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider
from app.core.llm_router.router import LLMRouter
from app.models import AgentDecision, Source, Ticket
from app.services.agents.escalation_classify import (
    EscalationClassifyError,
    EscalationTriple,
    classify_escalation_payload,
    classify_escalation_ticket,
    triple_from_ticket,
)

BUG_JSON = '{"type": "Bug_fix", "confidence": 0.9, "reason": "操作正确仍报错"}'
DEMAND_JSON = '{"type": "Demand", "confidence": 0.92, "reason": "AI 说不支持，客户要支持"}'


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        # 断言三元组确实进了 user prompt
        user = messages[-1].content
        assert "客户原始问题" in user and "AI 客服回答" in user and "客户不满反馈" in user
        return LLMResponse(
            content=self._content,
            provider=self.name,
            model="fake",
            input_tokens=10,
            output_tokens=5,
        )


def _router(content: str) -> LLMRouter:
    return LLMRouter([_FakeProvider(content)])


_TRIPLE = EscalationTriple(
    original_question="开票点了没反应",
    ai_answer="请确认已完成税局认证后在开具处操作",
    dissatisfaction="认证早做了，点了还是没反应",
)


def test_classify_payload_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    res = classify_escalation_payload(_TRIPLE, router=_router(BUG_JSON))
    assert res.type == "Bug_fix"
    assert res.confidence == 0.9


def test_classify_payload_demand() -> None:
    res = classify_escalation_payload(_TRIPLE, router=_router(DEMAND_JSON))
    assert res.type == "Demand"


def test_invalid_type_raises() -> None:
    with pytest.raises(EscalationClassifyError, match="invalid type"):
        classify_escalation_payload(
            _TRIPLE, router=_router('{"type": "Whatever", "confidence": 0.5}')
        )


def test_non_json_raises() -> None:
    with pytest.raises(EscalationClassifyError, match="non-JSON"):
        classify_escalation_payload(_TRIPLE, router=_router("我觉得是 bug"))


def test_triple_from_ticket_reads_source_payload(db_session: Session) -> None:
    db_session.add(Source(code="ai_cs", name="AI 客服"))
    db_session.commit()
    t = Ticket(
        short_code="TKT-ESC-1",
        source_code="ai_cs",
        source_ticket_id="sess-1",
        type="Raw",
        status="received",
        title="q",
        body="开票点了没反应",
        source_payload={
            "ai_cs": {
                "original_question": "开票点了没反应",
                "ai_answer": "确认认证后操作",
                "dissatisfaction": "做了没用",
            }
        },
    )
    db_session.add(t)
    db_session.commit()
    triple = triple_from_ticket(t)
    assert triple.ai_answer == "确认认证后操作"
    assert triple.dissatisfaction == "做了没用"


def _patch_router(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    from app.services.agents import escalation_classify as mod

    monkeypatch.setattr(
        mod.LLMRouter, "from_settings", classmethod(lambda _cls, **_kw: _router(content))
    )


def test_classify_ticket_writes_predicted_and_audit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Source(code="ai_cs", name="AI 客服"))
    db_session.commit()
    t = Ticket(
        short_code="TKT-ESC-2",
        source_code="ai_cs",
        source_ticket_id="sess-2",
        type="Raw",
        status="received",
        title="开票点了没反应",
        body="开票点了没反应",
        source_payload={
            "ai_cs": {
                "original_question": "开票点了没反应",
                "ai_answer": "确认认证",
                "dissatisfaction": "做了没用",
            }
        },
    )
    db_session.add(t)
    db_session.commit()
    _patch_router(monkeypatch, BUG_JSON)

    res = classify_escalation_ticket(t.id, db=db_session)
    assert res is not None and res.type == "Bug_fix"
    db_session.refresh(t)
    assert t.predicted_type == "Bug_fix"
    assert float(t.predicted_confidence) == 0.9
    d = db_session.query(AgentDecision).filter_by(subject_id=t.id).one()
    assert d.proposal["agent"] == "escalation_classify_v1"
    assert d.proposal["source"] == "ai_cs_escalation"
