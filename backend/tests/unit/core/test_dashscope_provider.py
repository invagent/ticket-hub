"""DashScopeLLMProvider tests — mock at HTTP layer via respx.

The provider reuses GLMClient (DashScope compatible-mode speaks the same
OpenAI dialect), so these tests pin: base_url routing, model default,
cost table lookup, and error translation.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.llm_router import LLMMessage, LLMRouter
from app.core.llm_router.providers.base import ProviderRetryableError
from app.core.llm_router.providers.dashscope import (
    DASHSCOPE_BASE_URL,
    DashScopeLLMProvider,
    _calc_cost,
)


class _Settings:
    dashscope_api_key = "sk-test"
    dashscope_model = ""


def _ok_body(model: str = "deepseek-v4-flash") -> dict:
    return {
        "id": "x",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"type":"Bug_fix"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def test_from_settings_defaults_to_v4_flash() -> None:
    p = DashScopeLLMProvider.from_settings(_Settings())
    assert p._client._cfg.base_url == DASHSCOPE_BASE_URL
    assert p._client._cfg.default_model == "deepseek-v4-flash"


def test_from_settings_model_override() -> None:
    class S(_Settings):
        dashscope_model = "deepseek-v4-pro"

    p = DashScopeLLMProvider.from_settings(S())
    assert p._client._cfg.default_model == "deepseek-v4-pro"


@respx.mock
def test_complete_hits_dashscope_endpoint() -> None:
    route = respx.post(f"{DASHSCOPE_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body())
    )
    p = DashScopeLLMProvider.from_settings(_Settings())
    r = p.complete([LLMMessage(role="user", content="hi")])
    assert route.called
    assert r.provider == "dashscope"
    assert r.model == "deepseek-v4-flash"
    assert r.cost_usd > 0  # priced model → non-zero cost


@respx.mock
def test_network_error_is_retryable() -> None:
    respx.post(f"{DASHSCOPE_BASE_URL}/chat/completions").mock(
        side_effect=httpx.ConnectError("boom")
    )
    p = DashScopeLLMProvider.from_settings(_Settings())
    with pytest.raises(ProviderRetryableError):
        p.complete([LLMMessage(role="user", content="hi")])


def test_calc_cost_known_and_unknown_models() -> None:
    assert _calc_cost("deepseek-v4-flash", 1000, 1000) > 0
    assert _calc_cost("some-unknown-model", 1000, 1000) == 0.0


def _both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "glm_api_key", "sk-glm", raising=False)
    monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-ds", raising=False)
    monkeypatch.setattr(get_settings(), "llm_provider_order", "dashscope,glm", raising=False)


@respx.mock
def test_router_fallback_dashscope_down_glm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认顺序 dashscope 在前；其网络故障 → 路由器自动落到 GLM。"""
    _both_keys(monkeypatch)
    respx.post(f"{DASHSCOPE_BASE_URL}/chat/completions").mock(
        side_effect=httpx.ConnectError("dashscope down")
    )
    respx.post("https://open.bigmodel.cn/api/paas/v4/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body(model="glm-4.5-flash"))
    )
    router = LLMRouter.from_settings()
    r = router.complete([LLMMessage(role="user", content="hi")], agent="t")
    assert r.provider == "glm"


def test_provider_order_setting_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    _both_keys(monkeypatch)
    assert [p.name for p in LLMRouter.from_settings()._providers] == ["dashscope", "glm"]
    monkeypatch.setattr(get_settings(), "llm_provider_order", "glm,dashscope", raising=False)
    assert [p.name for p in LLMRouter.from_settings()._providers] == ["glm", "dashscope"]
    # 配了 key 但 order 没写到的 provider 仍兜底加入队尾
    monkeypatch.setattr(get_settings(), "llm_provider_order", "glm", raising=False)
    assert [p.name for p in LLMRouter.from_settings()._providers] == ["glm", "dashscope"]


def test_from_settings_only_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _both_keys(monkeypatch)
    router = LLMRouter.from_settings(only="dashscope")
    assert [p.name for p in router._providers] == ["dashscope"]
    with pytest.raises(RuntimeError, match="not configured"):
        LLMRouter.from_settings(only="nope")
