"""LinearWebhookClient — 转研发出口的飞书 webhook 客户端。

不同于 LinearClient（直连 Linear GraphQL），本客户端把「BUG/需求转产研」
以约定的 `{"fields": {...}}` JSON POST 到飞书侧 webhook（access_token 内嵌于 url）。
飞书侧再落 Linear / 建单。

复用 Linear adapter 的异常体系，便于 linear_push 统一 except 分支：
  - 401/403 → LinearAuthError
  - 其他 4xx/5xx / 业务失败 → LinearBusinessError
  - timeout / DNS / refused → LinearNetworkError
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

from .exceptions import LinearAuthError, LinearBusinessError, LinearNetworkError

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class LinearWebhookConfig:
    url: str
    token: str = ""
    timeout_seconds: float = 30.0

    @classmethod
    def from_settings(cls, settings: Any) -> LinearWebhookConfig:
        return cls(
            url=getattr(settings, "linear_webhook_url", ""),
            token=getattr(settings, "linear_webhook_token", ""),
            timeout_seconds=getattr(settings, "linear_webhook_timeout_seconds", 30.0),
        )


class LinearWebhookClient:
    def __init__(
        self,
        config: LinearWebhookConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._cfg = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> LinearWebhookClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def send_ticket(self, fields: dict[str, Any]) -> dict[str, Any]:
        """POST {"fields": fields}。返回响应 JSON（失败抛对应异常）。"""
        url = self._cfg.url
        # token 若单独配置且 url 未带 access_token，则附加为 query 参数
        if self._cfg.token and "access_token=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}access_token={self._cfg.token}"
        try:
            resp = self._http.post(url, json={"fields": fields})
        except httpx.TimeoutException as e:
            raise LinearNetworkError(f"webhook timeout: {e}") from e
        except httpx.HTTPError as e:  # connect/DNS/refused 等
            raise LinearNetworkError(f"webhook network error: {e}") from e

        if resp.status_code in (401, 403):
            raise LinearAuthError(
                f"webhook auth failed: HTTP {resp.status_code} — access_token 无效或无权限"
            )
        if resp.status_code >= 400:
            raise LinearBusinessError(
                f"webhook returned HTTP {resp.status_code}: {resp.text[:500]}"
            )
        try:
            body: dict[str, Any] = resp.json()
        except ValueError:
            # 非 JSON 响应：只要 2xx 视为成功，包一层返回
            return {"raw": resp.text}
        return body
