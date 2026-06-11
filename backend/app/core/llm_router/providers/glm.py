"""GLMLLMProvider — wraps adapters/glm.GLMClient as an LLMProvider.

Cost calculation (USD per 1k tokens, snapshot 2026-05; admin should
update when 智谱 changes pricing):

    glm-4.5-flash : input $0.0001 / output $0.0001  (cheapest)
    glm-4-air     : input $0.0007 / output $0.0007
    glm-4-plus    : input $0.0070 / output $0.0070

Pricing source is updated by editing _PRICING below; D3-A introduces a
proper `agent_runs.cost_usd` audit trail; for D3-B we just compute it
inline for the router log line.
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

# (input USD/1k, output USD/1k)
_PRICING: dict[str, tuple[float, float]] = {
    "glm-4.5-flash": (0.0001, 0.0001),
    "glm-4-air": (0.0007, 0.0007),
    "glm-4-plus": (0.007, 0.007),
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model)
    if pricing is None:
        return 0.0
    in_per_k, out_per_k = pricing
    return round((input_tokens / 1000.0) * in_per_k + (output_tokens / 1000.0) * out_per_k, 6)


class GLMLLMProvider(LLMProvider):
    name = "glm"

    def __init__(self, client: GLMClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Any) -> GLMLLMProvider:
        cfg = GLMConfig.from_settings(settings)
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
            raise ProviderError(f"glm auth: {e}") from e
        except GLMNetworkError as e:
            raise ProviderRetryableError(f"glm network: {e}") from e
        except GLMBusinessError as e:
            # Treat 5xx as retryable; everything else as fatal.
            code = getattr(e, "error_code", "") or ""
            if code.startswith("5"):
                raise ProviderRetryableError(f"glm 5xx: {e}") from e
            raise ProviderError(f"glm business: {e}") from e

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
