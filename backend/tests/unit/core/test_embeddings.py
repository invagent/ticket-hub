"""EmbeddingClient tests — respx-mocked HTTP, failover, settings wiring."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import get_settings
from app.core.llm_router.embeddings import (
    EmbeddingClient,
    EmbeddingError,
    _Endpoint,
)

_DS_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_GLM_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"


def _ds_endpoint() -> _Endpoint:
    return _Endpoint(
        name="dashscope",
        base_url=_DS_URL.rsplit("/", 1)[0],
        api_key="k1",
        model="text-embedding-v4",
    )


def _glm_endpoint() -> _Endpoint:
    return _Endpoint(
        name="glm", base_url=_GLM_URL.rsplit("/", 1)[0], api_key="k2", model="embedding-3"
    )


def _ok_payload(vectors: list[list[float]]) -> dict:  # type: ignore[type-arg]
    return {
        "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
        "usage": {"total_tokens": 12},
    }


@respx.mock
def test_embed_happy_path() -> None:
    respx.post(_DS_URL).mock(
        return_value=httpx.Response(200, json=_ok_payload([[0.1, 0.2], [0.3, 0.4]]))
    )
    client = EmbeddingClient(endpoints=[_ds_endpoint()])
    res = client.embed(["a", "b"])
    assert res.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert res.provider == "dashscope"
    assert res.model == "text-embedding-v4"
    assert res.total_tokens == 12
    assert res.cost_usd > 0


@respx.mock
def test_embed_orders_by_index() -> None:
    payload = {
        "data": [
            {"index": 1, "embedding": [9.0]},
            {"index": 0, "embedding": [1.0]},
        ],
        "usage": {"total_tokens": 2},
    }
    respx.post(_DS_URL).mock(return_value=httpx.Response(200, json=payload))
    res = EmbeddingClient(endpoints=[_ds_endpoint()]).embed(["x", "y"])
    assert res.vectors == [[1.0], [9.0]]


@respx.mock
def test_embed_failover_to_second_provider() -> None:
    respx.post(_DS_URL).mock(return_value=httpx.Response(500, text="boom"))
    respx.post(_GLM_URL).mock(return_value=httpx.Response(200, json=_ok_payload([[1.0]])))
    res = EmbeddingClient(endpoints=[_ds_endpoint(), _glm_endpoint()]).embed(["x"])
    assert res.provider == "glm"
    assert res.model == "embedding-3"


@respx.mock
def test_embed_all_fail_raises() -> None:
    respx.post(_DS_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(EmbeddingError, match="all embedding providers failed"):
        EmbeddingClient(endpoints=[_ds_endpoint()]).embed(["x"])


@respx.mock
def test_embed_count_mismatch_raises() -> None:
    respx.post(_DS_URL).mock(return_value=httpx.Response(200, json=_ok_payload([[1.0]])))
    with pytest.raises(EmbeddingError, match="all embedding providers failed"):
        EmbeddingClient(endpoints=[_ds_endpoint()]).embed(["x", "y"])


def test_embed_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="at least one text"):
        EmbeddingClient(endpoints=[_ds_endpoint()]).embed([])


def test_from_settings_no_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest already blanks the keys; just assert the contract.
    get_settings.cache_clear()
    try:
        with pytest.raises(EmbeddingError, match="no embedding provider configured"):
            EmbeddingClient.from_settings()
    finally:
        get_settings.cache_clear()


def test_from_settings_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLM_API_KEY", "k-glm")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k-ds")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "glm,dashscope")
    get_settings.cache_clear()
    try:
        client = EmbeddingClient.from_settings()
        assert [e.name for e in client.endpoints] == ["glm", "dashscope"]
        assert client.endpoints[0].model == "embedding-3"
        assert client.endpoints[1].model == "text-embedding-v4"
    finally:
        get_settings.cache_clear()
