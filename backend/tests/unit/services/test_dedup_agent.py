"""Dedup agent tests (D3-E) — cosine math, recall filtering, LLM-output
validation, and DB write paths with fake embedding + LLM providers.

No real network anywhere: EmbeddingClient.from_settings and
LLMRouter.from_settings are monkeypatched per test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider
from app.core.llm_router.router import LLMRouter
from app.models import AgentDecision, Source, Ticket, TicketEmbedding
from app.services.agents.dedup import (
    Candidate,
    DedupError,
    cosine_similarity,
    detect_ticket_duplicate,
    judge_duplicate_payload,
    recall_candidates,
    upsert_ticket_embedding,
)

# ---- cosine ------------------------------------------------------------------


def test_cosine_identical() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_dim_mismatch_returns_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0


def test_cosine_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---- LLM judge parsing ---------------------------------------------------------

DUP_JSON = (
    '{"decision": "duplicate", "duplicate_of_ticket_id": 101,'
    ' "confidence": 0.9, "reason": "同一故障"}'
)
NEW_JSON = (
    '{"decision": "new", "duplicate_of_ticket_id": null, "confidence": 0.85, "reason": "不同问题"}'
)


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
            cost_usd=0.001,
        )


def _router_with(content: str) -> LLMRouter:
    return LLMRouter([_FakeProvider(content)])


_CANDS = [Candidate(ticket_id=101, short_code="TKT-000101", title="同步停滞", similarity=0.91)]


def test_judge_duplicate_happy() -> None:
    parsed, _cost, model = judge_duplicate_payload(
        title="进项发票没同步",
        body="",
        candidates=_CANDS,
        candidate_bodies={101: "昨天还好的"},
        router=_router_with(DUP_JSON),
    )
    assert parsed["decision"] == "duplicate"
    assert parsed["duplicate_of_ticket_id"] == 101
    assert model == "fake-model"


def test_judge_new_happy() -> None:
    parsed, _, _ = judge_duplicate_payload(
        title="x", body="y", candidates=_CANDS, candidate_bodies={}, router=_router_with(NEW_JSON)
    )
    assert parsed["decision"] == "new"
    assert parsed["duplicate_of_ticket_id"] is None


def test_judge_rejects_id_outside_candidates() -> None:
    bad = '{"decision": "duplicate", "duplicate_of_ticket_id": 999, "confidence": 0.9}'
    with pytest.raises(DedupError, match="not among candidates"):
        judge_duplicate_payload(
            title="x", body="y", candidates=_CANDS, candidate_bodies={}, router=_router_with(bad)
        )


def test_judge_rejects_invalid_decision() -> None:
    with pytest.raises(DedupError, match="invalid decision"):
        judge_duplicate_payload(
            title="x",
            body="y",
            candidates=_CANDS,
            candidate_bodies={},
            router=_router_with('{"decision": "maybe", "confidence": 0.5}'),
        )


def test_judge_rejects_non_json() -> None:
    with pytest.raises(DedupError, match="non-JSON"):
        judge_duplicate_payload(
            title="x",
            body="y",
            candidates=_CANDS,
            candidate_bodies={},
            router=_router_with("应该是重复的"),
        )


def test_judge_duplicate_without_id_rejected() -> None:
    bad = '{"decision": "duplicate", "duplicate_of_ticket_id": null, "confidence": 0.9}'
    with pytest.raises(DedupError, match="integer duplicate_of_ticket_id"):
        judge_duplicate_payload(
            title="x", body="y", candidates=_CANDS, candidate_bodies={}, router=_router_with(bad)
        )


# ---- DB paths -------------------------------------------------------------------


class _FakeEmbeddingClient:
    """Returns a fixed vector regardless of input text."""

    def __init__(self, vector: list[float], model: str = "fake-emb") -> None:
        self._vector = vector
        self.model = model

    def embed(self, texts):  # type: ignore[no-untyped-def]
        from app.core.llm_router.embeddings import EmbeddingResult

        return EmbeddingResult(
            vectors=[self._vector for _ in texts],
            provider="fake",
            model=self.model,
            total_tokens=8,
            cost_usd=0.0001,
        )


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _make_ticket(db: Session, n: int, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"TKT-DDP-{n}",
        "source_code": "ksm",
        "source_ticket_id": f"ddp-{n}",
        "type": "Raw",
        "status": "received",
        "title": f"工单 {n}",
        "body": "",
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _embed_row(db: Session, ticket: Ticket, vector: list[float]) -> None:
    db.add(TicketEmbedding(ticket_id=ticket.id, model="fake-emb", dim=len(vector), vector=vector))
    db.commit()


def _patch_embedding(monkeypatch: pytest.MonkeyPatch, vector: list[float]) -> None:
    from app.core.llm_router.embeddings import EmbeddingClient

    monkeypatch.setattr(
        EmbeddingClient,
        "from_settings",
        classmethod(lambda _cls: _FakeEmbeddingClient(vector)),
    )


def _patch_router(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    from app.services.agents import dedup as mod

    monkeypatch.setattr(
        mod.LLMRouter,
        "from_settings",
        classmethod(lambda _cls, **_kw: _router_with(content)),
    )


def test_upsert_embedding_create_then_update(world: Session) -> None:
    t = _make_ticket(world, 1)
    upsert_ticket_embedding(world, t, client=_FakeEmbeddingClient([1.0, 0.0]))  # type: ignore[arg-type]
    row = world.get(TicketEmbedding, t.id)
    assert row is not None and row.vector == [1.0, 0.0] and row.dim == 2

    upsert_ticket_embedding(world, t, client=_FakeEmbeddingClient([0.0, 1.0, 0.0]))  # type: ignore[arg-type]
    world.refresh(row)
    assert row.vector == [0.0, 1.0, 0.0] and row.dim == 3


def test_recall_filters_threshold_self_deleted_and_child(world: Session) -> None:
    me = _make_ticket(world, 10)
    near = _make_ticket(world, 11)
    far = _make_ticket(world, 12)
    gone = _make_ticket(world, 13)
    parent = _make_ticket(world, 15)
    child = Ticket(
        short_code="TKT-DDP-14",
        type="Child",
        status="received",
        internal_split_id=f"{parent.short_code}-C1",
        parent_ticket_id=parent.id,
        title="child",
    )
    world.add(child)
    world.commit()

    _embed_row(world, me, [1.0, 0.0])
    _embed_row(world, near, [0.99, 0.14])  # cos ≈ 0.99
    _embed_row(world, far, [0.0, 1.0])  # cos = 0
    _embed_row(world, gone, [1.0, 0.01])
    _embed_row(world, child, [1.0, 0.0])  # identical but Child → excluded
    gone.deleted_at = datetime.now(UTC)
    world.commit()

    hits = recall_candidates(world, me, [1.0, 0.0], threshold=0.8, top_k=5, pool=200)
    assert [c.ticket_id for c in hits] == [near.id]
    assert hits[0].similarity > 0.98


def test_detect_no_candidates_writes_dedup_new(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_embedding(monkeypatch, [1.0, 0.0])
    t = _make_ticket(world, 20)
    res = detect_ticket_duplicate(t.id, db=world)
    assert res is not None
    assert res.decision == "new"
    assert res.method == "recall_only"

    d = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=t.id).one()
    assert d.decision_type == "dedup_new"
    assert d.proposal["method"] == "recall_only"
    assert d.proposal["cost_usd"] == 0.0
    # 向量已落库
    assert world.get(TicketEmbedding, t.id) is not None


def test_detect_duplicate_writes_dedup_link(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _make_ticket(world, 30, title="全票池没同步")
    _embed_row(world, old, [1.0, 0.0])
    new = _make_ticket(world, 31, title="进项发票没有同步进来")

    _patch_embedding(monkeypatch, [0.99, 0.1])
    _patch_router(
        monkeypatch,
        '{"decision": "duplicate", "duplicate_of_ticket_id": '
        + str(old.id)
        + ', "confidence": 0.9, "reason": "同一故障"}',
    )
    res = detect_ticket_duplicate(new.id, db=world)
    assert res is not None
    assert res.decision == "duplicate"
    assert res.duplicate_of_ticket_id == old.id
    assert res.method == "llm"

    d = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=new.id).one()
    assert d.decision_type == "dedup_link"
    assert d.proposal["duplicate_of_ticket_id"] == old.id
    assert d.proposal["candidates"][0]["ticket_id"] == old.id
    # 仅审计，不动工单
    world.refresh(new)
    assert new.status == "received" and new.hub_issue_id is None


def test_detect_llm_new_writes_dedup_new(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _make_ticket(world, 40)
    _embed_row(world, old, [1.0, 0.0])
    new = _make_ticket(world, 41)

    _patch_embedding(monkeypatch, [0.95, 0.3])
    _patch_router(monkeypatch, NEW_JSON)
    res = detect_ticket_duplicate(new.id, db=world)
    assert res is not None
    assert res.decision == "new"
    assert res.method == "llm"
    d = world.query(AgentDecision).filter_by(subject_type="ticket", subject_id=new.id).one()
    assert d.decision_type == "dedup_new"
    assert len(d.proposal["candidates"]) == 1


def test_detect_invalid_llm_output_swallowed(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _make_ticket(world, 50)
    _embed_row(world, old, [1.0, 0.0])
    new = _make_ticket(world, 51)

    _patch_embedding(monkeypatch, [1.0, 0.0])
    _patch_router(
        monkeypatch,
        '{"decision": "duplicate", "duplicate_of_ticket_id": 999999, "confidence": 0.9}',
    )
    assert detect_ticket_duplicate(new.id, db=world) is None
    assert world.query(AgentDecision).filter_by(subject_id=new.id).count() == 0


def test_detect_embedding_failure_swallowed(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.llm_router.embeddings import EmbeddingClient, EmbeddingError

    def _boom(_cls: type) -> EmbeddingClient:
        raise EmbeddingError("no provider")

    monkeypatch.setattr(EmbeddingClient, "from_settings", classmethod(_boom))
    t = _make_ticket(world, 60)
    assert detect_ticket_duplicate(t.id, db=world) is None
    assert world.query(AgentDecision).filter_by(subject_id=t.id).count() == 0


def test_detect_skips_child_ticket(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _make_ticket(world, 70)
    child = Ticket(
        short_code="TKT-DDP-71",
        type="Child",
        status="received",
        internal_split_id=f"{parent.short_code}-C1",
        parent_ticket_id=parent.id,
        title="child",
    )
    world.add(child)
    world.commit()
    _patch_embedding(monkeypatch, [1.0, 0.0])
    assert detect_ticket_duplicate(child.id, db=world) is None
    assert world.query(AgentDecision).filter_by(subject_id=child.id).count() == 0
