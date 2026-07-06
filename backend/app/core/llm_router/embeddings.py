"""Embedding client — hub 级语义去重召回（原 D3-E ticket dedup，ADR-0016 已退役）.

Both configured LLM vendors expose OpenAI-dialect /embeddings endpoints
(POST {base}/embeddings → {"data": [{"embedding": [...]}, ...], "usage": ...}),
so one small httpx client serves DashScope (text-embedding-v4) and GLM
(embedding-3). Failover order follows LLM_PROVIDER_ORDER, same as chat.

Deliberately NOT part of the LLMProvider protocol: embeddings have a
different request/response shape and only one consumer (hub_dedup recall).
If a third consumer appears, promote to providers/.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.core.llm_router.providers.dashscope import DASHSCOPE_BASE_URL
from app.core.logging import get_logger

logger = get_logger(__name__)

GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# USD per 1k tokens — 2026-06 snapshot at ~7.2 CNY/USD; admin updates on change.
_PRICING: dict[str, float] = {
    "text-embedding-v4": 0.00007,
    "embedding-3": 0.00007,
}


class EmbeddingError(Exception):
    """All embedding providers failed."""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine; returns 0.0 on dim mismatch or zero vector (treat as
    no-signal rather than erroring — mismatches happen when the embedding
    model changes between deployments)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str = ""
    model: str = ""
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True, frozen=True)
class _Endpoint:
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass(slots=True)
class EmbeddingClient:
    """Walks endpoints in order; returns the first success."""

    endpoints: list[_Endpoint] = field(default_factory=list)
    timeout_seconds: float = 30.0

    @classmethod
    def from_settings(cls) -> EmbeddingClient:
        settings = get_settings()
        available: dict[str, _Endpoint] = {}
        if settings.dashscope_api_key:
            available["dashscope"] = _Endpoint(
                name="dashscope",
                base_url=DASHSCOPE_BASE_URL,
                api_key=settings.dashscope_api_key,
                model=settings.dashscope_embedding_model,
            )
        if settings.glm_api_key:
            available["glm"] = _Endpoint(
                name="glm",
                base_url=GLM_BASE_URL,
                api_key=settings.glm_api_key,
                model=settings.glm_embedding_model,
            )
        order = [n.strip() for n in settings.llm_provider_order.split(",") if n.strip()]
        endpoints = [available[n] for n in order if n in available]
        endpoints += [e for n, e in available.items() if n not in order]
        if not endpoints:
            raise EmbeddingError(
                "no embedding provider configured (set DASHSCOPE_API_KEY or GLM_API_KEY)"
            )
        return cls(endpoints=endpoints)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            raise ValueError("embed() needs at least one text")
        errors: list[str] = []
        for ep in self.endpoints:
            try:
                return self._call(ep, texts)
            except (httpx.TransportError, EmbeddingError) as e:
                logger.warning(
                    "embedding_call_failed", provider=ep.name, model=ep.model, error=str(e)
                )
                errors.append(f"{ep.name}: {e}")
        raise EmbeddingError(f"all embedding providers failed: {'; '.join(errors)}")

    def _call(self, ep: _Endpoint, texts: list[str]) -> EmbeddingResult:
        resp = httpx.post(
            f"{ep.base_url}/embeddings",
            headers={"Authorization": f"Bearer {ep.api_key}"},
            json={"model": ep.model, "input": texts},
            timeout=self.timeout_seconds,
        )
        if not resp.is_success:
            raise EmbeddingError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingError(f"unexpected embeddings payload: {str(body)[:200]}")
        # OpenAI dialect: data items carry an `index` — order by it defensively.
        ordered = sorted(data, key=lambda d: int(d.get("index", 0)))
        vectors = [[float(x) for x in item["embedding"]] for item in ordered]
        total_tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
        cost = round((total_tokens / 1000.0) * _PRICING.get(ep.model, 0.0), 6)
        logger.info(
            "embedding_call_ok",
            provider=ep.name,
            model=ep.model,
            texts=len(texts),
            total_tokens=total_tokens,
            cost_usd=cost,
        )
        return EmbeddingResult(
            vectors=vectors,
            provider=ep.name,
            model=ep.model,
            total_tokens=total_tokens,
            cost_usd=cost,
        )
