"""AiCsClient — HTTP client for the self-built AI 客服 open-api.

Wire format (skill-management.json, 2026-07-03):
  - Auth: GET /open-api/get_token?appid&create_time&sign  where
      sign = MD5(appid + create_time + app_key)  (create_time = unix seconds)
    returns {token, expires_in}; token goes in the `token:` header (24h TTL).
    We cache it and refresh a bit before expiry.
  - Envelope: every response is {errcode, description, data};
    errcode == "0000" (STRING) is success, anything else is a business error.
  - Skills are multi-file with a draft→published→superseded lifecycle;
    versions are `{skill_name}:V{N}`.

Errors:
  - token acquisition / 401 / 403      → AiCsAuthError
  - errcode != "0000"                  → AiCsBusinessError (carries errcode)
  - HTTP 4xx/5xx / non-JSON            → AiCsBusinessError
  - timeout / DNS / refused            → AiCsNetworkError
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.logging import get_logger

from .exceptions import AiCsAuthError, AiCsBusinessError, AiCsNetworkError
from .types import (
    AiCsConfig,
    DraftSummary,
    ReplayResult,
    SkillDetail,
    SkillFile,
    SkillSummary,
    SkillVersion,
)

logger = get_logger(__name__)

_OK = "0000"
# Refresh the token this many seconds before it actually expires.
_TOKEN_REFRESH_MARGIN = 300.0


class AiCsClient:
    def __init__(
        self,
        config: AiCsConfig,
        *,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> AiCsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- skill version management -----------------------------------

    def list_skills(self) -> list[SkillSummary]:
        data = self._request("GET", "/open-api/skills")
        return [_parse_skill_summary(row) for row in (data or [])]

    def get_skill(self, name: str) -> SkillDetail:
        data = self._request("GET", f"/open-api/skills/{name}")
        return _parse_skill_detail(data or {})

    def list_drafts(self, name: str) -> list[DraftSummary]:
        data = self._request("GET", f"/open-api/skills/{name}/drafts")
        return [_parse_draft_summary(row) for row in (data or [])]

    def create_draft(
        self,
        name: str,
        *,
        files: list[dict[str, str]],
        operator: str,
        reason: str,
    ) -> str:
        """Create a draft off the current published version. Empty `files`
        inherits all published files; non-empty upserts on top. Returns the
        new version string (e.g. `customer-service:V4`)."""
        data = self._request(
            "POST",
            f"/open-api/skills/{name}/drafts",
            json={"files": files, "operator": operator, "reason": reason},
        )
        return str((data or {}).get("version") or "")

    def update_draft(
        self,
        name: str,
        version: str,
        *,
        files: list[dict[str, str]],
        operator: str,
        reason: str,
    ) -> None:
        self._request(
            "PUT",
            f"/open-api/skills/{name}/drafts/{version}",
            json={"files": files, "operator": operator, "reason": reason},
        )

    def publish_draft(self, name: str, version: str) -> None:
        """Publish a draft: draft→published, old published→superseded, files
        written to disk so new AI 客服 sessions pick it up immediately."""
        self._request("POST", f"/open-api/skills/{name}/drafts/{version}/publish")

    def rollback(self, name: str, *, version: str, operator: str, reason: str) -> str:
        """Copy a historical version's content into a NEW draft (does not
        publish). Returns the new draft version — caller must publish it."""
        data = self._request(
            "POST",
            f"/open-api/skills/{name}/rollback",
            json={"version": version, "operator": operator, "reason": reason},
        )
        return str((data or {}).get("version") or "")

    # ---- replay -----------------------------------------------------

    def replay(
        self,
        *,
        session_id: str | None = None,
        question: str | None = None,
        skill: str | None = None,
        use_latest_knowledge: bool = True,
        skill_draft_version: str | None = None,
    ) -> ReplayResult:
        """Re-answer a question with the current (or a draft) skill + latest
        knowledge. Pass `session_id` to reuse the original session's question,
        or `question`+`skill` directly. `skill_draft_version` tests an
        unpublished draft against production KB without touching prod."""
        if not session_id and not question:
            raise ValueError("replay requires either session_id or question")
        body: dict[str, Any] = {"use_latest_knowledge": use_latest_knowledge}
        if session_id:
            body["session_id"] = session_id
        if question:
            body["question"] = question
        if skill:
            body["skill"] = skill
        if skill_draft_version:
            body["skill_draft_version"] = skill_draft_version
        data = self._request("POST", "/open-api/replay", json=body)
        d = data or {}
        cited = d.get("cited_knowledge")
        used = d.get("skills_used")
        return ReplayResult(
            answer=str(d.get("answer") or ""),
            cited_knowledge=cited if isinstance(cited, list) else [],
            skills_used=[str(s) for s in used] if isinstance(used, list) else [],
            trace_id=str(d.get("trace_id") or ""),
        )

    # ---- auth -------------------------------------------------------

    def _ensure_token(self) -> str:
        if self._token and self._clock() < self._token_expires_at:
            return self._token
        create_time = str(int(self._clock()))
        sign = hashlib.md5(
            f"{self._cfg.app_id}{create_time}{self._cfg.app_key}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        try:
            resp = self._http.get(
                f"{self._cfg.base_url}/open-api/get_token",
                params={"appid": self._cfg.app_id, "create_time": create_time, "sign": sign},
                timeout=self._cfg.timeout_seconds,
            )
        except httpx.TransportError as e:
            raise AiCsNetworkError(f"network error getting AI 客服 token: {e}") from e
        if resp.status_code in (401, 403):
            raise AiCsAuthError(f"AI 客服 token auth failed ({resp.status_code})")
        data = self._unwrap(resp)
        token = str((data or {}).get("token") or "")
        if not token:
            raise AiCsAuthError("AI 客服 get_token returned empty token")
        try:
            ttl = float((data or {}).get("expires_in") or 0)
        except (TypeError, ValueError):
            ttl = 0.0
        self._token = token
        self._token_expires_at = self._clock() + max(ttl - _TOKEN_REFRESH_MARGIN, 0.0)
        return token

    # ---- transport --------------------------------------------------

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        token = self._ensure_token()
        try:
            resp = self._http.request(
                method,
                f"{self._cfg.base_url}{path}",
                headers={"token": token, "Content-Type": "application/json"},
                json=json,
                timeout=self._cfg.timeout_seconds,
            )
        except httpx.TransportError as e:
            raise AiCsNetworkError(f"network error calling AI 客服 {path}: {e}") from e
        if resp.status_code in (401, 403):
            raise AiCsAuthError(f"AI 客服 auth failed ({resp.status_code}) on {path}")
        return self._unwrap(resp)

    def _unwrap(self, resp: httpx.Response) -> Any:
        """Validate HTTP + envelope, return the `data` payload."""
        if not resp.is_success:
            raise AiCsBusinessError(
                f"AI 客服 HTTP {resp.status_code}: {resp.text[:200]}",
                error_code=str(resp.status_code),
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise AiCsBusinessError(f"AI 客服 non-JSON response: {e}") from e
        errcode = str(body.get("errcode"))
        if errcode != _OK:
            raise AiCsBusinessError(
                str(body.get("description") or "AI 客服 business error"),
                error_code=errcode,
            )
        return body.get("data")


# ---- envelope → DTO parsers -----------------------------------------


def _parse_files(rows: Any) -> list[SkillFile]:
    out: list[SkillFile] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append(
            SkillFile(
                filename=str(r.get("filename") or ""),
                filepath=str(r.get("filepath") or ""),
                content=r.get("content") if isinstance(r.get("content"), str) else None,
            )
        )
    return out


def _parse_skill_summary(row: dict[str, Any]) -> SkillSummary:
    return SkillSummary(
        skill_name=str(row.get("skill_name") or ""),
        published_version=str(row.get("published_version") or ""),
        operator=str(row.get("operator") or ""),
        updated_at=str(row.get("updated_at") or ""),
        files=_parse_files(row.get("files")),
    )


def _parse_version(row: dict[str, Any]) -> SkillVersion:
    return SkillVersion(
        version=str(row.get("version") or ""),
        status=str(row.get("status") or ""),
        operator=str(row.get("operator") or ""),
        reason=str(row.get("reason") or ""),
        created_at=str(row.get("created_at") or ""),
    )


def _parse_skill_detail(data: dict[str, Any]) -> SkillDetail:
    published = data.get("published") or {}
    history = [_parse_version(v) for v in (data.get("history") or []) if isinstance(v, dict)]
    return SkillDetail(
        skill_name=str(data.get("skill_name") or ""),
        published_version=str(published.get("version") or ""),
        published_operator=str(published.get("operator") or ""),
        published_reason=str(published.get("reason") or ""),
        published_files=_parse_files(published.get("files")),
        history=history,
    )


def _parse_draft_summary(row: dict[str, Any]) -> DraftSummary:
    return DraftSummary(
        version=str(row.get("version") or ""),
        operator=str(row.get("operator") or ""),
        reason=str(row.get("reason") or ""),
        created_at=str(row.get("created_at") or ""),
        files=_parse_files(row.get("files")),
    )
