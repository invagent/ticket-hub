"""Feishu SSO callback orchestration.

OAuth2 authorization code flow:
    1. Browser hits  GET /api/auth/feishu/login  → returns authorize_url
    2. User scans, Feishu redirects to redirect_uri with `?code=...`
    3. POST /api/auth/feishu/callback?code=...
       - Exchange code for `user_access_token` via /authen/v1/oidc/access_token
       - Fetch user profile via /authen/v1/user_info
       - Upsert into `users` table by feishu_uid
       - Sign + return JWT

Decision D19: Feishu SSO is the only login entry. No password fallback.

Tests stub the two Feishu endpoints with respx; this service has no global state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import User

logger = get_logger(__name__)


_FEISHU_BASE = "https://open.feishu.cn"


class FeishuSSOError(Exception):
    """Auth flow failed (network, malformed response, or invalid code)."""


@dataclass(slots=True, frozen=True)
class FeishuSSOConfig:
    app_id: str
    app_secret: str
    redirect_uri: str
    base_url: str = _FEISHU_BASE


@dataclass(slots=True, frozen=True)
class AuthenticatedUser:
    user_id: int
    feishu_uid: str
    name: str
    email: str | None
    role: str


class FeishuSSOService:
    """Stateless service. One per request is fine; safe to instantiate per call."""

    def __init__(
        self,
        config: FeishuSSOConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._cfg = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> FeishuSSOService:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- step 1: app-level token (tenant_access_token) -----------------

    def _get_app_access_token(self) -> str:
        try:
            resp = self._http.post(
                f"{self._cfg.base_url}/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": self._cfg.app_id, "app_secret": self._cfg.app_secret},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FeishuSSOError(f"app_access_token failed: {e}") from e
        token = resp.json().get("app_access_token")
        if not token:
            raise FeishuSSOError("app_access_token empty in response")
        return str(token)

    # ---- step 2: code → user_access_token -----------------------------

    def _exchange_code(self, code: str, app_access_token: str) -> dict[str, Any]:
        try:
            resp = self._http.post(
                f"{self._cfg.base_url}/open-apis/authen/v1/oidc/access_token",
                headers={"Authorization": f"Bearer {app_access_token}"},
                json={"grant_type": "authorization_code", "code": code},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FeishuSSOError(f"oidc/access_token failed: {e}") from e
        body = resp.json()
        if body.get("code") != 0:
            raise FeishuSSOError(
                f"oidc/access_token returned code={body.get('code')} msg={body.get('msg')}"
            )
        data = body.get("data") or {}
        if not data.get("access_token"):
            raise FeishuSSOError("oidc/access_token: empty user access_token")
        return dict(data)

    # ---- step 3: fetch profile ----------------------------------------

    def _fetch_user_info(self, user_access_token: str) -> dict[str, Any]:
        try:
            resp = self._http.get(
                f"{self._cfg.base_url}/open-apis/authen/v1/user_info",
                headers={"Authorization": f"Bearer {user_access_token}"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FeishuSSOError(f"user_info failed: {e}") from e
        body = resp.json()
        if body.get("code") != 0:
            raise FeishuSSOError(
                f"user_info returned code={body.get('code')} msg={body.get('msg')}"
            )
        return dict(body.get("data") or {})

    # ---- step 4: upsert user ------------------------------------------

    def upsert_user(self, db: Session, profile: dict[str, Any]) -> User:
        """Find by feishu_uid (open_id), create if missing.

        Decision D19: feishu_uid is the immutable primary identity key.
        We do NOT update feishu_uid; we update name / email / mobile if changed.
        """
        feishu_uid = profile.get("open_id") or profile.get("user_id")
        if not feishu_uid:
            raise FeishuSSOError("user_info has neither open_id nor user_id")

        user = db.query(User).filter(User.feishu_uid == feishu_uid).one_or_none()
        if user is None:
            user = User(
                feishu_uid=feishu_uid,
                name=profile.get("name") or profile.get("en_name") or feishu_uid,
                email=profile.get("email"),
                mobile=profile.get("mobile"),
                employee_no=profile.get("employee_no"),
                role="member",  # role assignment happens via /admin/users
            )
            db.add(user)
            db.flush()
            logger.info("feishu_sso_user_created", feishu_uid=feishu_uid, user_id=user.id)
        else:
            # Reactivate soft-deleted users on re-login (per upgrade_plan §4.12)
            changed = False
            if user.deleted_at is not None:
                user.deleted_at = None
                changed = True
            new_name = profile.get("name") or profile.get("en_name")
            if new_name and user.name != new_name:
                user.name = new_name
                changed = True
            new_email = profile.get("email")
            if new_email and user.email != new_email:
                user.email = new_email
                changed = True
            if changed:
                db.flush()
                logger.info("feishu_sso_user_updated", feishu_uid=feishu_uid, user_id=user.id)
        return user

    # ---- public entrypoint -------------------------------------------

    def login(self, db: Session, *, code: str) -> AuthenticatedUser:
        """Run the full callback flow + upsert user. Caller commits."""
        if not code:
            raise FeishuSSOError("empty authorization code")
        app_token = self._get_app_access_token()
        token_data = self._exchange_code(code, app_token)
        profile = self._fetch_user_info(token_data["access_token"])
        user = self.upsert_user(db, profile)
        return AuthenticatedUser(
            user_id=user.id,
            feishu_uid=user.feishu_uid,
            name=user.name,
            email=user.email,
            role=user.role,
        )
