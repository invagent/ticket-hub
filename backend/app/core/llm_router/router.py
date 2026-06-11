"""LLMRouter implementation — D3-B.

The router walks `providers` in order; for each call it logs the outcome
with structured fields (provider/model/agent/latency/tokens/cost). On
retryable failures it moves to the next provider. Cost calculation is
provider-specific and lives in each Provider's `calculate_cost`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger

from .providers.base import LLMProvider, ProviderError, ProviderRetryableError
from .providers.dashscope import DashScopeLLMProvider
from .providers.glm import GLMLLMProvider
from .types import LLMMessage, LLMResponse

logger = get_logger(__name__)


class LLMRouterError(Exception):
    """All providers failed (or the only provider failed non-retryably)."""

    def __init__(self, message: str, *, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


class LLMRouter:
    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("LLMRouter needs at least one provider")
        self._providers = list(providers)

    @classmethod
    def from_settings(cls, *, only: str | None = None) -> LLMRouter:
        """Build the default router from environment settings.

        Provider order comes from LLM_PROVIDER_ORDER (default
        "dashscope,glm" — deepseek-v4-flash won the 2026-06-11 eval:
        85% vs glm-4-flash 73.3% / glm-4.5-flash 80%). Only providers
        whose API key is configured are instantiated, so a missing key
        won't crash startup.

        `only` restricts to a single provider by name ("glm"/"dashscope") —
        used by eval scripts for provider A/B comparison.
        """
        settings = get_settings()
        available: dict[str, LLMProvider] = {}
        if settings.glm_api_key:
            available["glm"] = GLMLLMProvider.from_settings(settings)
        if settings.dashscope_api_key:
            available["dashscope"] = DashScopeLLMProvider.from_settings(settings)
        # TODO: openai / anthropic providers join this dict.

        order = [name.strip() for name in settings.llm_provider_order.split(",") if name.strip()]
        providers = [available[name] for name in order if name in available]
        # Keys configured but missing from the order string still join (at the back).
        providers += [p for name, p in available.items() if name not in order]

        if only is not None:
            providers = [p for p in providers if p.name == only]
            if not providers:
                raise RuntimeError(f"provider {only!r} not configured (missing API key?)")
        if not providers:
            raise RuntimeError("No LLM provider configured (set GLM_API_KEY or another *_API_KEY)")
        return cls(providers)

    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        agent: str,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call providers in order. Returns first successful response."""
        attempts: list[dict[str, Any]] = []
        last_exc: Exception | None = None

        for p in self._providers:
            t0 = time.monotonic()
            try:
                resp = p.complete(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "llm_router_call_ok",
                    provider=p.name,
                    model=resp.model,
                    agent=agent,
                    latency_ms=latency_ms,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    cost_usd=resp.cost_usd,
                )
                attempts.append(
                    {
                        "provider": p.name,
                        "ok": True,
                        "latency_ms": latency_ms,
                    }
                )
                resp.provider = p.name
                return resp
            except ProviderRetryableError as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "llm_router_call_retry",
                    provider=p.name,
                    agent=agent,
                    latency_ms=latency_ms,
                    error=str(e),
                )
                attempts.append(
                    {
                        "provider": p.name,
                        "ok": False,
                        "retryable": True,
                        "latency_ms": latency_ms,
                        "error": str(e),
                    }
                )
                last_exc = e
                continue
            except ProviderError as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.exception(
                    "llm_router_call_failed",
                    provider=p.name,
                    agent=agent,
                    latency_ms=latency_ms,
                    error=str(e),
                )
                attempts.append(
                    {
                        "provider": p.name,
                        "ok": False,
                        "retryable": False,
                        "latency_ms": latency_ms,
                        "error": str(e),
                    }
                )
                # Non-retryable: bail without trying other providers.
                raise LLMRouterError(
                    f"{p.name} failed (non-retryable): {e}", attempts=attempts
                ) from e

        # All providers exhausted with retryable errors
        raise LLMRouterError(
            "all providers exhausted with retryable failures",
            attempts=attempts,
        ) from last_exc
