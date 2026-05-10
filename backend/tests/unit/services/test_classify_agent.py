"""Tests for ticket classification agent (D3-C).

Pure-function tests + DB-write tests using a respx-mocked GLM endpoint.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider, ProviderError
from app.core.llm_router.router import LLMRouter
from app.models import AgentDecision, Source, Ticket
from app.services.agents.classify import (
    ClassifyError,
    classify_payload,
    classify_ticket,
)

GLM_BASE = "https://open.bigmodel.cn/api/paas/v4"


# ---- pure parsing / validation -------------------------------------------


class _FakeProvider(LLMProvider):
    """Stub provider that returns a fixed JSON content."""

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


def test_classify_payload_happy_path() -> None:
    router = _router_with('{"type": "Bug_fix", "confidence": 0.95, "reason": "明显报错"}')
    res = classify_payload(
        title="接口报错",
        body="调用发票云接口返回 500",
        product_line_code="cloud-fapiao",
        module="接口集成",
        router=router,
    )
    assert res.type == "Bug_fix"
    assert res.confidence == 0.95
    assert res.reason == "明显报错"
    assert res.cost_usd == 0.001
    assert res.model == "fake-model"


def test_classify_payload_invalid_type_raises() -> None:
    router = _router_with('{"type": "Unknown", "confidence": 0.9}')
    with pytest.raises(ClassifyError, match="invalid type"):
        classify_payload(title="x", body="y", product_line_code="p", module="m", router=router)


def test_classify_payload_confidence_out_of_range() -> None:
    router = _router_with('{"type": "Demand", "confidence": 1.5}')
    with pytest.raises(ClassifyError, match="confidence out of range"):
        classify_payload(title="x", body="y", product_line_code="p", module="m", router=router)


def test_classify_payload_non_json() -> None:
    router = _router_with("not json")
    with pytest.raises(ClassifyError, match="non-JSON"):
        classify_payload(title="x", body="y", product_line_code="p", module="m", router=router)


def test_classify_payload_missing_confidence() -> None:
    router = _router_with('{"type": "Demand"}')
    with pytest.raises(ClassifyError, match="missing/invalid confidence"):
        classify_payload(title="x", body="y", product_line_code="p", module="m", router=router)


# ---- DB-side tests --------------------------------------------------------


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _make_ticket(db: Session, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-CLS-1",
        "source_code": "ksm",
        "source_ticket_id": "cls-1",
        "type": "Raw",
        "status": "received",
        "title": "接口报错",
        "body": "调用发票云接口失败",
        "product_line_code": None,
        "module": "接口集成",
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_classify_ticket_writes_back(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Pure-Python: monkeypatch LLMRouter.from_settings to return a fake."""
    from app.services.agents import classify as cls_mod

    monkeypatch.setattr(
        cls_mod.LLMRouter,
        "from_settings",
        classmethod(
            lambda _cls: _router_with('{"type": "Bug_fix", "confidence": 0.92, "reason": "ok"}')
        ),
    )
    t = _make_ticket(world)
    res = classify_ticket(t.id, db=world)
    assert res is not None
    assert res.type == "Bug_fix"
    world.refresh(t)
    assert t.predicted_type == "Bug_fix"
    assert t.predicted_confidence == Decimal("0.92")
    assert t.classified_at is not None

    # D3-A: an agent_decisions row was written in the same transaction.
    decisions = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=t.id).all()
    assert len(decisions) == 1
    d = decisions[0]
    assert d.decision_type == "classify_type"
    assert d.status == "executed"
    assert d.reverted_at is None
    assert d.proposal["predicted_type"] == "Bug_fix"
    assert d.proposal["confidence"] == 0.92
    assert d.proposal["reason"] == "ok"
    assert d.proposal["model"] == "fake-model"


def test_classify_ticket_swallows_router_error(
    world: Session,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """All-providers-failed → returns None, ticket left unchanged."""
    from app.services.agents import classify as cls_mod

    failing = _FakeProvider("", raises=ProviderError("auth"))
    monkeypatch.setattr(
        cls_mod.LLMRouter,
        "from_settings",
        classmethod(lambda _cls: LLMRouter([failing])),
    )
    t = _make_ticket(world, short_code="TKT-CLS-FAIL", source_ticket_id="fail-1")
    res = classify_ticket(t.id, db=world)
    assert res is None
    world.refresh(t)
    assert t.predicted_type is None
    assert t.classified_at is None
    # D3-A: failed classification must not leave an audit row.
    assert world.query(AgentDecision).filter_by(subject_id=t.id).count() == 0


def test_classify_ticket_404_silent(world: Session) -> None:
    """Non-existent ticket id → log warning + None, no crash."""
    res = classify_ticket(99999, db=world)
    assert res is None


def test_classify_ticket_invalid_llm_output_silent(
    world: Session,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    from app.services.agents import classify as cls_mod

    monkeypatch.setattr(
        cls_mod.LLMRouter,
        "from_settings",
        classmethod(lambda _cls: _router_with("not json at all")),
    )
    t = _make_ticket(world, short_code="TKT-CLS-BAD", source_ticket_id="bad-1")
    res = classify_ticket(t.id, db=world)
    assert res is None
    world.refresh(t)
    assert t.predicted_type is None


# ---- e2e via real GLM HTTP shape (respx) ---------------------------------


@respx.mock
def test_classify_payload_via_real_router_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """End-to-end including the full GLMProvider→GLMClient HTTP path."""
    monkeypatch.setenv("GLM_API_KEY", "sk-test")
    from app.config import get_settings

    get_settings.cache_clear()

    respx.post(f"{GLM_BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "model": "glm-4.5-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"type":"Demand","confidence":0.88,"reason":"功能扩展"}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
            },
        )
    )
    res = classify_payload(
        title="希望支持自定义字段",
        body="客户想加一个字段到发票池",
        product_line_code="cloud-fapiao",
        module="全票池同步",
    )
    assert res.type == "Demand"
    assert res.confidence == 0.88
    assert res.cost_usd > 0
