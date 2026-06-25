"""hub_dedup 测试 — 召回过滤 + LLM 确认 + push 层 supersede。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.llm_router import LLMResponse
from app.core.llm_router.providers import LLMProvider
from app.core.llm_router.router import LLMRouter
from app.models import HubIssue
from app.services.hub_issues import hub_dedup


class _FakeEmb:
    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    def embed(self, texts):  # type: ignore[no-untyped-def]
        from app.core.llm_router.embeddings import EmbeddingResult

        return EmbeddingResult(vectors=[self._vec for _ in texts], model="fake")

    def close(self) -> None:
        pass


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, content: str) -> None:
        self._c = content

    def complete(self, messages, **kw):  # type: ignore[no-untyped-def]
        return LLMResponse(content=self._c, provider="fake", model="fake")


def _router(content: str) -> LLMRouter:
    return LLMRouter([_FakeProvider(content)])


def _hub(db: Session, n: int, **ov) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"HUB-HD-{n}",
        "type": "Bug_fix",
        "title": f"问题 {n}",
        "canonical_body": "描述",
        "status": "created",
        "product_line_code": "cloud-fapiao",
    }
    base.update(ov)
    h = HubIssue(**base)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def test_no_candidates_returns_none(db_session: Session) -> None:
    hub = _hub(db_session, 1)
    out = hub_dedup.find_duplicate_hub(
        db_session, hub, embedding_client=_FakeEmb([1.0, 0.0]), router=_router("{}")
    )  # type: ignore[arg-type]
    assert out is None
    db_session.refresh(hub)
    assert hub.embedding == [1.0, 0.0]  # 顺带存了向量


def test_dedup_hit(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # 已推 Linear 的历史 hub（同产品线 + 有 embedding）
    existing = _hub(
        db_session, 10, linear_uuid="u", linear_identifier="CNPRD-1", embedding=[1.0, 0.0]
    )
    new = _hub(db_session, 11)
    monkeypatch.setattr(hub_dedup, "load_prompt", lambda name: "判定提示词")
    out = hub_dedup.find_duplicate_hub(
        db_session,
        new,
        embedding_client=_FakeEmb([0.99, 0.1]),  # 与 existing 高相似
        router=_router(f'{{"is_dup": true, "dup_hub_id": {existing.id}, "confidence": 0.9}}'),
    )  # type: ignore[arg-type]
    assert out == existing.id


def test_not_pushed_hub_not_candidate(db_session: Session) -> None:
    # 未推 Linear 的 hub 不作候选
    _hub(db_session, 20, linear_uuid=None, embedding=[1.0, 0.0])
    new = _hub(db_session, 21)
    out = hub_dedup.find_duplicate_hub(
        db_session, new, embedding_client=_FakeEmb([1.0, 0.0]), router=_router("{}")
    )  # type: ignore[arg-type]
    assert out is None


def test_different_product_not_candidate(db_session: Session) -> None:
    _hub(db_session, 30, linear_uuid="u", embedding=[1.0, 0.0], product_line_code="other")
    new = _hub(db_session, 31)
    out = hub_dedup.find_duplicate_hub(
        db_session, new, embedding_client=_FakeEmb([1.0, 0.0]), router=_router("{}")
    )  # type: ignore[arg-type]
    assert out is None


def test_below_threshold_no_llm(db_session: Session) -> None:
    _hub(db_session, 40, linear_uuid="u", embedding=[0.0, 1.0])  # 正交，余弦=0
    new = _hub(db_session, 41)
    out = hub_dedup.find_duplicate_hub(
        db_session, new, embedding_client=_FakeEmb([1.0, 0.0]), router=_router("BAD")
    )  # type: ignore[arg-type]
    assert out is None  # 没候选过阈值，不调 LLM


def test_llm_says_not_dup(db_session: Session) -> None:
    _hub(db_session, 50, linear_uuid="u", embedding=[1.0, 0.0])
    new = _hub(db_session, 51)
    out = hub_dedup.find_duplicate_hub(
        db_session,
        new,
        embedding_client=_FakeEmb([0.99, 0.05]),
        router=_router('{"is_dup": false, "dup_hub_id": null}'),
    )  # type: ignore[arg-type]
    assert out is None


def test_embedding_unavailable_degrades(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.llm_router.embeddings import EmbeddingClient, EmbeddingError

    def _boom(_cls):  # type: ignore[no-untyped-def]
        raise EmbeddingError("no provider")

    monkeypatch.setattr(EmbeddingClient, "from_settings", classmethod(_boom))
    hub = _hub(db_session, 60)
    out = hub_dedup.find_duplicate_hub(db_session, hub)
    assert out is None  # 降级不去重
