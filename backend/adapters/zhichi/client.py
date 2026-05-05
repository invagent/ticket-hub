"""ZhichiClient — class-based migration of feishu-python/app/zhichi_client.py.

Behavior parity:
  * MD5 sign: md5(appid + create_time + app_key)
  * Token TTL: server-provided expires_in - 5min margin (else 24h fallback)
  * Two retry triggers, each force-refreshes once:
      - HTTP 401
      - ret_code in (100001, 100002) or "token" in ret_msg.lower() (and not 000000)
  * `get_agent_by_name` caches the agent_list for 30 min

Differences:
  * Class-based; per-instance TokenCache + agent cache
  * httpx; typed Agent / ReplyTicketRequest DTOs
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from app.core.logging import get_logger

from .._token_cache import TokenCache
from .exceptions import ZhichiAuthError, ZhichiBusinessError
from .types import Agent, ReplyTicketRequest, ZhichiConfig

logger = get_logger(__name__)

_AGENT_CACHE_TTL_SECONDS = 30 * 60
_TOKEN_FALLBACK_TTL_SECONDS = 24 * 60 * 60


class ZhichiClient:
    def __init__(
        self,
        config: ZhichiConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._cfg = config
        self._timeout = timeout_seconds
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)
        self._token_cache = TokenCache(name="zhichi.token")
        # agent list cache (separate from token cache; longer TTL)
        self._agent_lock = threading.Lock()
        self._agent_cache: list[Agent] = []
        self._agent_expires_at = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> ZhichiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- auth ----------------------------------------------------------

    def _refresh_token(self) -> tuple[str, float]:
        create_time = str(int(time.time()))
        sign = hashlib.md5(
            f"{self._cfg.appid}{create_time}{self._cfg.app_key}".encode()
        ).hexdigest()
        try:
            resp = self._http.get(
                f"{self._cfg.base_url}/api/get_token",
                params={"appid": self._cfg.appid, "create_time": create_time, "sign": sign},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ZhichiAuthError(f"token refresh failed: {e}") from e
        body = resp.json()
        if body.get("ret_code") != "000000":
            raise ZhichiAuthError(f"token endpoint returned ret_code={body.get('ret_code')}")
        item = body.get("item") or {}
        token = str(item.get("token") or "")
        if not token:
            raise ZhichiAuthError("empty token in response")
        try:
            ttl = int(item.get("expires_in") or _TOKEN_FALLBACK_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = _TOKEN_FALLBACK_TTL_SECONDS
        return token, float(ttl)

    def _get_token(self, *, force: bool = False) -> str:
        return self._token_cache.get(self._refresh_token, force=force)

    # ---- request helper ------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Auto-handle HTTP 401 + ret_code token expiry. Single force-refresh retry."""

        extra_headers = dict(kwargs.pop("headers", {}) or {})

        def do(token: str) -> dict[str, Any] | None:
            headers = {**extra_headers, "token": token, "content-type": "application/json"}
            resp = self._http.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401:
                return None
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

        result = do(self._get_token())
        if result is None:
            logger.warning("zhichi_http_401_retry")
            result = do(self._get_token(force=True))
            if result is None:
                raise ZhichiAuthError("HTTP 401 after force-refresh")
        elif _is_token_expired(result):
            logger.warning("zhichi_ret_code_token_expired_retry", ret_code=result.get("ret_code"))
            retry = do(self._get_token(force=True))
            if retry is None:
                raise ZhichiAuthError("HTTP 401 on retry after token expiry")
            result = retry
        return result

    # ---- public ops ----------------------------------------------------

    def get_ticket_by_id(self, ticket_id: str) -> dict[str, Any]:
        body = self._request(
            "GET",
            f"{self._cfg.base_url}/api/ws/5/ticket/get_ticket_by_id",
            params={"ticketid": ticket_id},
        )
        if body.get("ret_code") != "000000":
            raise ZhichiBusinessError(
                op="get_ticket_by_id",
                ret_code=str(body.get("ret_code", "")),
                ret_msg=str(body.get("ret_msg", "")),
            )
        return dict(body.get("item") or {})

    def list_agents(self, *, force_refresh: bool = False) -> list[Agent]:
        """Return all 智齿 agents; cached for 30 minutes."""
        return self._cached_agents(force=force_refresh, now=time.time)

    def _cached_agents(self, *, force: bool, now: Callable[[], float]) -> list[Agent]:
        ts = now()
        if not force and self._agent_cache and ts < self._agent_expires_at:
            return list(self._agent_cache)
        with self._agent_lock:
            ts = now()
            if not force and self._agent_cache and ts < self._agent_expires_at:
                return list(self._agent_cache)
            body = self._request("GET", f"{self._cfg.base_url}/api/ws/5/ticket/get_data_dict")
            if body.get("ret_code") != "000000":
                raise ZhichiBusinessError(
                    op="get_data_dict",
                    ret_code=str(body.get("ret_code", "")),
                    ret_msg=str(body.get("ret_msg", "")),
                )
            agents_raw = (body.get("item") or {}).get("agent_list", []) or []
            self._agent_cache = [Agent.from_dict(d) for d in agents_raw]
            self._agent_expires_at = ts + _AGENT_CACHE_TTL_SECONDS
            return list(self._agent_cache)

    def get_agent_by_name(self, name: str) -> Agent | None:
        for a in self.list_agents():
            if a.agent_name == name:
                return a
        return None

    def upload_file(self, file_name: str, file_content: bytes) -> str:
        """Upload attachment; returns file_url. Same dual-retry pattern as `_request`."""

        def do(token: str) -> dict[str, Any] | None:
            resp = self._http.post(
                f"{self._cfg.base_url}/api/ws/5/ticket/upload_file",
                headers={"token": token},
                files={"file": (file_name, file_content)},
                data={"file_num_key": file_name},
                timeout=60.0,
            )
            if resp.status_code == 401:
                return None
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

        result = do(self._get_token())
        if result is None:
            result = do(self._get_token(force=True))
            if result is None:
                raise ZhichiAuthError("upload_file 401 after force-refresh")
        elif _is_token_expired(result):
            retry = do(self._get_token(force=True))
            if retry is None:
                raise ZhichiAuthError("upload_file 401 on retry after token expiry")
            result = retry
        if result.get("ret_code") != "000000":
            raise ZhichiBusinessError(
                op="upload_file",
                ret_code=str(result.get("ret_code", "")),
                ret_msg=str(result.get("ret_msg", "")),
            )
        return str((result.get("item") or {}).get("file_url", ""))

    def reply_ticket(self, req: ReplyTicketRequest) -> dict[str, Any]:
        payload = {
            "ticketid": req.ticket_id,
            "ticket_title": req.ticket_title,
            "ticket_content": req.ticket_content,
            "get_ticket_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reply_content": req.reply_content,
            "reply_type": req.reply_type,
            "reply_file_str": req.reply_file_str,
            "reply_agentid": req.reply_agentid,
            "reply_agent_name": req.reply_agent_name,
            "ticket_status": req.ticket_status,
            "ticket_level": req.ticket_level,
        }
        body = self._request(
            "POST",
            f"{self._cfg.base_url}/api/ws/5/ticket/save_ticket_reply",
            json=payload,
        )
        if body.get("ret_code") != "000000":
            raise ZhichiBusinessError(
                op="save_ticket_reply",
                ret_code=str(body.get("ret_code", "")),
                ret_msg=str(body.get("ret_msg", "")),
            )
        return body


def _is_token_expired(result: dict[str, Any]) -> bool:
    """parity with feishu-python: ret_code 100001/100002 OR 'token' in ret_msg, except 000000."""
    code = str(result.get("ret_code", ""))
    if code == "000000":
        return False
    if code in ("100001", "100002"):
        return True
    msg = str(result.get("ret_msg", "")).lower()
    return "token" in msg
