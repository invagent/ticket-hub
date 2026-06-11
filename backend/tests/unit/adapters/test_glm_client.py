"""GLM HTTP client tests with respx mocks."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.glm import (
    ChatMessage,
    ChatRequest,
    GLMAuthError,
    GLMBusinessError,
    GLMClient,
    GLMConfig,
    GLMNetworkError,
)

BASE = "https://open.bigmodel.cn/api/paas/v4"


def _cfg() -> GLMConfig:
    return GLMConfig(api_key="sk-test", base_url=BASE, default_model="glm-4.5-flash")


def _client() -> GLMClient:
    return GLMClient(_cfg(), http_client=httpx.Client(timeout=5.0))


def _req() -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
    )


def test_config_from_settings_reads_model() -> None:
    class S:
        glm_api_key = "sk-x"
        glm_model = "glm-4-flash"

    cfg = GLMConfig.from_settings(S())
    assert cfg.default_model == "glm-4-flash"


def test_config_from_settings_model_defaults_when_blank() -> None:
    class S:
        glm_api_key = "sk-x"
        glm_model = ""  # .env 留空 → 回落默认

    assert GLMConfig.from_settings(S()).default_model == "glm-4.5-flash"


@respx.mock
def test_chat_success() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "20260509-x",
                "model": "glm-4.5-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi there"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        )
    )
    with _client() as c:
        r = c.chat(_req())
    assert r.text == "hi there"
    assert r.model == "glm-4.5-flash"
    assert r.usage.prompt_tokens == 5
    assert r.usage.completion_tokens == 2
    assert r.first.finish_reason == "stop"


@respx.mock
def test_chat_uses_default_model_when_unset() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "glm-4.5-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    with _client() as c:
        c.chat(_req())
    assert captured["model"] == "glm-4.5-flash"


@respx.mock
def test_chat_auth_error_401() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "invalid api_key"})
    )
    with _client() as c, pytest.raises(GLMAuthError):
        c.chat(_req())


@respx.mock
def test_chat_business_500() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(500, text="internal server error")
    )
    with _client() as c, pytest.raises(GLMBusinessError) as ei:
        c.chat(_req())
    assert ei.value.error_code == "500"


@respx.mock
def test_chat_business_error_in_200_body() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"error": {"code": "1234", "message": "bad request"}},
        )
    )
    with _client() as c, pytest.raises(GLMBusinessError) as ei:
        c.chat(_req())
    assert ei.value.error_code == "1234"


def test_network_error() -> None:
    # No respx mock → real network call → DNS or connection error
    cfg = GLMConfig(
        api_key="x", base_url="http://localhost:1/no-server", default_model="glm-4.5-flash"
    )
    with GLMClient(cfg, http_client=httpx.Client(timeout=1.0)) as c, pytest.raises(GLMNetworkError):
        c.chat(_req())


@respx.mock
def test_response_format_passed_through() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "glm-4.5-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    req = ChatRequest(
        messages=[ChatMessage(role="user", content="give me JSON")],
        response_format={"type": "json_object"},
    )
    with _client() as c:
        c.chat(req)
    assert captured["response_format"] == {"type": "json_object"}
