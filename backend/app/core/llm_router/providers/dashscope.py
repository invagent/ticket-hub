"""DashScopeLLMProvider — 阿里云百炼 (DashScope) OpenAI-compatible mode.

Reuses adapters/glm.GLMClient: DashScope's compatible-mode endpoint speaks
the exact same OpenAI chat-completions dialect as 智谱 BigModel (Bearer
auth, /chat/completions, choices/usage envelope), so a separate HTTP
client would be 200 duplicated lines. If the dialects ever diverge,
promote a dedicated adapters/dashscope/ package.

Default model: deepseek-v4-flash (hosted DeepSeek v4, cheap tier — fits
the < $0.05/ticket cost target). Override via DASHSCOPE_MODEL.

Pricing: DashScope bills in CNY per 1k tokens and changes prices often —
values below are a 2026-06 snapshot converted at ~7.2 CNY/USD. Admin
updates _PRICING when 阿里云 adjusts pricing.
"""

from __future__ import annotations

from typing import Any

from adapters.glm import (
    ChatMessage,
    ChatRequest,
    GLMAuthError,
    GLMBusinessError,
    GLMClient,
    GLMConfig,
    GLMNetworkError,
)
from app.core.llm_router.types import LLMMessage, LLMResponse

from .base import LLMProvider, ProviderError, ProviderRetryableError

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# (input USD/1k, output USD/1k) — snapshot 2026-06, admin updates on change.
_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.00006, 0.00045),
    "deepseek-v4-pro": (0.0006, 0.0024),
    "deepseek-v3.2": (0.0002, 0.0004),
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model)
    if pricing is None:
        return 0.0
    in_per_k, out_per_k = pricing
    return round((input_tokens / 1000.0) * in_per_k + (output_tokens / 1000.0) * out_per_k, 6)


class DashScopeLLMProvider(LLMProvider):
    name = "dashscope"

    def __init__(self, client: GLMClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Any) -> DashScopeLLMProvider:
        cfg = GLMConfig(
            api_key=getattr(settings, "dashscope_api_key", ""),
            base_url=DASHSCOPE_BASE_URL,
            default_model=getattr(settings, "dashscope_model", "") or "deepseek-v4-flash",
        )
        return cls(GLMClient(cfg))

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        req = ChatRequest(
            messages=[ChatMessage(role=m.role, content=m.content) for m in messages],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        try:
            r = self._client.chat(req)
        except GLMAuthError as e:
            raise ProviderError(f"dashscope auth: {e}") from e
        except GLMNetworkError as e:
            raise ProviderRetryableError(f"dashscope network: {e}") from e
        except GLMBusinessError as e:
            code = getattr(e, "error_code", "") or ""
            if code.startswith("5") or code == "429":
                raise ProviderRetryableError(f"dashscope retryable {code}: {e}") from e
            raise ProviderError(f"dashscope business: {e}") from e

        cost_usd = _calc_cost(r.model, r.usage.prompt_tokens, r.usage.completion_tokens)
        return LLMResponse(
            content=r.text,
            provider=self.name,
            model=r.model,
            input_tokens=r.usage.prompt_tokens,
            output_tokens=r.usage.completion_tokens,
            cost_usd=cost_usd,
            raw=r.raw,
        )
