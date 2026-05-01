"""LLM Router skeleton (decision D5, full impl in D3).

Contract:
  - `LLMRouter.complete(messages, *, agent: str, schema: dict | None) -> LLMResponse`
  - JSON Schema enforced; Provider-format normalization happens here.
  - Cost is tracked per call and persisted via agent_runs (D3).

D0 ships type stubs only so downstream code can import names.
"""

from .types import LLMMessage, LLMResponse  # noqa: F401
