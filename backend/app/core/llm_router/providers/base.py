"""Provider protocol + shared exception classes.

Concrete providers (glm.py, openai.py, ...) translate adapter errors
into ProviderError / ProviderRetryableError so the router can decide
whether to fall over to the next provider.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.llm_router.types import LLMMessage, LLMResponse


class ProviderError(Exception):
    """Non-retryable: auth failure, malformed request, business error."""


class ProviderRetryableError(Exception):
    """Network timeout, 5xx, rate-limit. Router should try next provider."""


@runtime_checkable
class LLMProvider(Protocol):
    """Each provider exposes a name + complete()."""

    name: str

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...
