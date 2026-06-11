"""GLMClient — 智谱 BigModel chat completions HTTP client.

Single endpoint POST /chat/completions:
  - Auth via Bearer token in Authorization header
  - JSON body OpenAI-compatible
  - Returns id / choices / usage

Errors:
  - 401/403 → GLMAuthError
  - other 4xx/5xx → GLMBusinessError
  - timeout / DNS / refused → GLMNetworkError
  - 200 but body has top-level `error` → GLMBusinessError
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

from .exceptions import (
    GLMAuthError,
    GLMBusinessError,
    GLMNetworkError,
)
from .types import (
    ChatChoice,
    ChatRequest,
    ChatResponse,
    GLMConfig,
    Usage,
)

logger = get_logger(__name__)


class GLMClient:
    def __init__(
        self,
        config: GLMConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._cfg = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> GLMClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------

    def chat(self, req: ChatRequest) -> ChatResponse:
        url = f"{self._cfg.base_url.rstrip('/')}/chat/completions"
        payload = req.to_payload(self._cfg.default_model)

        try:
            resp = self._http.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self._cfg.timeout_seconds,
            )
        except httpx.TransportError as e:
            raise GLMNetworkError(f"network error calling GLM: {e}") from e

        if resp.status_code in (401, 403):
            raise GLMAuthError(f"GLM auth failed ({resp.status_code}): {resp.text[:200]}")
        if not resp.is_success:
            raise GLMBusinessError(
                f"GLM HTTP {resp.status_code}: {resp.text[:200]}",
                error_code=str(resp.status_code),
            )

        try:
            body = resp.json()
        except ValueError as e:
            raise GLMBusinessError(f"GLM non-JSON response: {e}") from e

        # 200 with top-level `error` block (rare, but the v4 API can do it)
        if isinstance(body.get("error"), dict):
            err = body["error"]
            raise GLMBusinessError(
                str(err.get("message") or "GLM business error"),
                error_code=str(err.get("code")) if err.get("code") else None,
            )

        return _parse_response(body)

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }


def _parse_response(body: dict[str, Any]) -> ChatResponse:
    raw_choices = body.get("choices") or []
    choices: list[ChatChoice] = []
    for c in raw_choices:
        msg = c.get("message") or {}
        choices.append(
            ChatChoice(
                index=int(c.get("index", 0)),
                role=str(msg.get("role") or ""),
                content=str(msg.get("content") or ""),
                finish_reason=c.get("finish_reason"),
            )
        )
    usage_raw = body.get("usage") or {}
    usage = Usage(
        prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
        completion_tokens=int(usage_raw.get("completion_tokens", 0)),
        total_tokens=int(usage_raw.get("total_tokens", 0)),
    )
    return ChatResponse(
        id=str(body.get("id") or ""),
        model=str(body.get("model") or ""),
        choices=choices,
        usage=usage,
        raw=body,
    )
