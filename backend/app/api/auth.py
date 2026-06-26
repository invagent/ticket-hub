"""Feishu SSO login + JWT issuance.

Decision D19 (v0.5.3): Feishu SSO is the only login entry.

Callback flow (D2 fix):
  Feishu redirects browser → GET /api/auth/feishu/callback?code=xxx
  → exchange code for JWT
  → 302 redirect to frontend `${FRONTEND_BASE}#token=<jwt>&user_id=...`
  Token sits in URL fragment so it never reaches the server (no nginx/access
  log exposure, no Referer leak). Frontend bootstrap reads location.hash,
  writes localStorage.auth_token, then clears the hash.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.db import get_session
from app.services.auth.feishu_sso import (
    FeishuSSOConfig,
    FeishuSSOError,
    FeishuSSOService,
)

router = APIRouter()
logger = get_logger(__name__)


def _frontend_base_from_redirect(redirect_uri: str) -> str:
    """Derive frontend base URL from the configured callback URI.

    `https://yjcj.online/ticket-hub/api/auth/feishu/callback`
       → `https://yjcj.online/ticket-hub/`
    `http://localhost:8080/api/auth/feishu/callback`
       → `http://localhost:5173/`  (dev fallback uses vite default)
    """
    marker = "/api/auth/feishu/callback"
    if redirect_uri.endswith(marker):
        base = redirect_uri[: -len(marker)]
        if base in {"http://localhost:8080", "http://127.0.0.1:8080"}:
            return "http://localhost:5173/"
        return base + "/" if not base.endswith("/") else base
    # Fallback: same origin, root path
    return "/"


class LoginUrlResponse(BaseModel):
    authorize_url: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: int
    feishu_uid: str
    name: str
    role: str


@router.get("/feishu/login", response_model=LoginUrlResponse)
def feishu_login_url() -> LoginUrlResponse:
    """Return the Feishu OAuth2 authorize URL."""
    settings = get_settings()
    if not settings.feishu_app_id:
        raise HTTPException(status_code=503, detail="feishu_app_id not configured")
    url = (
        "https://open.feishu.cn/open-apis/authen/v1/authorize"
        f"?app_id={settings.feishu_app_id}"
        f"&redirect_uri={settings.feishu_sso_redirect_uri}"
    )
    return LoginUrlResponse(authorize_url=url)


@router.get("/feishu/callback")
def feishu_callback(code: str, db: Session = Depends(get_session)) -> RedirectResponse:
    """Exchange Feishu auth code for our JWT and redirect browser to frontend.

    Browser arrives here as a GET (Feishu's standard OAuth2 redirect). We
    exchange the code, sign a JWT, and 302 to the frontend SPA with token
    in URL fragment (so the token never reaches our server logs).

    Side effect: upserts users row (creates on first login).
    """
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise HTTPException(status_code=503, detail="feishu credentials not configured")

    svc = FeishuSSOService(
        FeishuSSOConfig(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            redirect_uri=settings.feishu_sso_redirect_uri,
        )
    )
    try:
        try:
            authed = svc.login(db, code=code)
        finally:
            svc.close()
    except FeishuSSOError as e:
        logger.warning("feishu_sso_failed", error=str(e))
        # Redirect to frontend with error fragment so user sees a friendly page,
        # rather than raw JSON 401.
        base = _frontend_base_from_redirect(settings.feishu_sso_redirect_uri)
        return RedirectResponse(
            url=f"{base}login#sso_error={quote(str(e))}",
            status_code=302,
        )

    db.commit()
    token, ttl = issue_jwt(sub=str(authed.user_id), name=authed.name, role=authed.role)

    base = _frontend_base_from_redirect(settings.feishu_sso_redirect_uri)
    fragment = urlencode(
        {
            "token": token,
            "user_id": authed.user_id,
            "name": authed.name,
            "role": authed.role,
            "feishu_uid": authed.feishu_uid,
            "expires_in": ttl,
        }
    )
    logger.info(
        "feishu_sso_success",
        user_id=authed.user_id,
        feishu_uid=authed.feishu_uid,
        role=authed.role,
    )
    return RedirectResponse(url=f"{base}#{fragment}", status_code=302)


def issue_jwt(*, sub: str, name: str, role: str = "member") -> tuple[str, int]:
    """Sign a JWT for an authenticated user. Tested standalone in D0."""
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=settings.jwt_ttl_seconds)
    payload = {
        "sub": sub,
        "name": name,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, settings.jwt_ttl_seconds
