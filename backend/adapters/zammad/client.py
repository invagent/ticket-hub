"""ZammadClient — minimal read-only HTTP client for Zammad REST API v1.

Primarily used for:
  - Verifying ticket existence (webhook payload validation)
  - Fetching full ticket detail when webhook payload is partial

Auth: Zammad HTTP Token Authentication (`Authorization: Token token=<api_token>`)
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

from .exceptions import ZammadAuthError, ZammadBusinessError, ZammadNetworkError
from .types import ZammadConfig

logger = get_logger(__name__)


class ZammadClient:
    def __init__(
        self,
        config: ZammadConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._cfg = config
        self._timeout = timeout_seconds
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> ZammadClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        """Fetch a single ticket by id. Returns raw JSON dict."""
        result = self._get(f"/api/v1/tickets/{ticket_id}")
        return result if isinstance(result, dict) else {}

    def list_tickets(self, *, page: int = 1, per_page: int = 25) -> list[dict[str, Any]]:
        """List tickets (paginated). Returns list of raw JSON dicts."""
        result = self._get(
            "/api/v1/tickets",
            params={"page": page, "per_page": per_page},
        )
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self._cfg.api_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._cfg.base_url.rstrip('/')}{path}"
        try:
            resp = self._http.get(url, headers=self._headers(), params=params)
        except httpx.TransportError as e:
            raise ZammadNetworkError(f"network error: {e}") from e

        if resp.status_code == 401:
            raise ZammadAuthError("invalid api_token")
        if resp.status_code == 403:
            raise ZammadAuthError("forbidden")
        if not resp.is_success:
            raise ZammadBusinessError(f"Zammad API error {resp.status_code}: {resp.text[:200]}")

        try:
            return resp.json()
        except ValueError as e:
            raise ZammadBusinessError(f"non-JSON response: {e}") from e

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self._cfg.base_url.rstrip('/')}{path}"
        try:
            resp = self._http.post(url, headers=self._headers(), json=body)
        except httpx.TransportError as e:
            raise ZammadNetworkError(f"network error: {e}") from e

        if resp.status_code in (401, 403):
            raise ZammadAuthError(f"auth error: {resp.status_code}")
        if not resp.is_success:
            raise ZammadBusinessError(f"Zammad API error {resp.status_code}: {resp.text[:200]}")

        try:
            return resp.json()
        except ValueError as e:
            raise ZammadBusinessError(f"non-JSON response: {e}") from e
