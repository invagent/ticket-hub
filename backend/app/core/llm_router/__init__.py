"""LLM Router — provider-abstracting facade for chat completions.

Public API:
    router = LLMRouter.from_settings()
    resp = router.complete([
        LLMMessage(role="user", content="hello"),
    ], agent="probe")

Failover:
    Each provider in order is tried. On retryable failure (network,
    rate-limit, 5xx), router moves to the next. Auth failures and
    business errors propagate immediately (no point retrying).

Structured logging:
    Every call writes `llm_router_call_{ok,failed}` with provider /
    model / agent / latency_ms / tokens / cost_usd. D3-A introduces
    `agent_runs` persistence; for now the logs are the audit trail.

D3-B: GLM (智谱 BigModel) is the only provider wired up. DeepSeek /
      OpenAI / Anthropic plug in as additional providers in later
      commits without API changes.
"""

from .router import LLMRouter, LLMRouterError
from .types import LLMMessage, LLMResponse

__all__ = [
    "LLMRouter",
    "LLMRouterError",
    "LLMMessage",
    "LLMResponse",
]
