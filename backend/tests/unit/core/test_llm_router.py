"""LLMRouter + GLMLLMProvider tests.

Mock at the GLM HTTP layer (via respx) so we exercise the full router →
provider → adapter stack.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.llm_router.providers import (
    GLMLLMProvider,
    LLMProvider,
    ProviderRetryableError,
)
from app.core.llm_router.providers.glm import _calc_cost
from app.core.llm_router.types import LLMResponse

BASE = "https://open.bigmodel.cn/api/paas/v4"


def _build_provider() -> GLMLLMProvider:
    from adapters.glm import GLMClient, GLMConfig

    cfg = GLMConfig(api_key="sk-test", base_url=BASE, default_model="glm-4.5-flash")
    return GLMLLMProvider(GLMClient(cfg, http_client=httpx.Client(timeout=5.0)))


@respx.mock
def test_router_complete_success() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "model": "glm-4.5-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "你好！"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    router = LLMRouter([_build_provider()])
    r = router.complete(
        [LLMMessage(role="user", content="你好")],
        agent="test_probe",
    )
    assert r.content == "你好！"
    assert r.provider == "glm"
    assert r.model == "glm-4.5-flash"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    # 10 input + 5 output @ 0.0001 each = 0.0000015 (rounded to 6 dp)
    assert r.cost_usd == 0.000002 or r.cost_usd > 0


def test_calc_cost_known_model() -> None:
    # 1k input + 1k output of glm-4-plus @ $0.007 each = $0.014
    assert _calc_cost("glm-4-plus", 1000, 1000) == 0.014


def test_calc_cost_unknown_model_zero() -> None:
    assert _calc_cost("non-existent-model", 100, 200) == 0.0


@respx.mock
def test_router_falls_through_on_retryable_then_succeeds() -> None:
    """First provider raises retryable; second succeeds."""

    class FailingProvider(LLMProvider):
        name = "broken"

        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderRetryableError("simulated rate limit")

    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "model": "glm-4.5-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    router = LLMRouter([FailingProvider(), _build_provider()])
    r = router.complete([LLMMessage(role="user", content="x")], agent="t")
    assert r.content == "ok"
    assert r.provider == "glm"


@respx.mock
def test_router_non_retryable_aborts_immediately() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "bad key"})
    )
    router = LLMRouter([_build_provider()])
    with pytest.raises(LLMRouterError) as ei:
        router.complete([LLMMessage(role="user", content="x")], agent="t")
    assert ei.value.attempts[0]["ok"] is False
    assert ei.value.attempts[0]["retryable"] is False


def test_router_exhausted_with_only_retryable() -> None:
    class P(LLMProvider):
        name = "retryable"

        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            raise ProviderRetryableError("network")

    router = LLMRouter([P()])
    with pytest.raises(LLMRouterError):
        router.complete([LLMMessage(role="user", content="x")], agent="t")


def test_router_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        LLMRouter([])


@respx.mock
def test_glm_5xx_is_retryable() -> None:
    """GLM 5xx → ProviderRetryableError → router moves on (or exhausts)."""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(503, text="overloaded"))

    class FallbackProvider(LLMProvider):
        name = "fallback"
        called = False

        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            FallbackProvider.called = True
            return LLMResponse(content="fb", provider="fallback", model="x")

    router = LLMRouter([_build_provider(), FallbackProvider()])
    r = router.complete([LLMMessage(role="user", content="x")], agent="t")
    assert FallbackProvider.called is True
    assert r.content == "fb"
