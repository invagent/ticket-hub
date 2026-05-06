"""Feishu SSO login + JWT issuance.

Decision D19 (v0.5.3): Feishu SSO is the only login entry.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/feishu/callback", response_model=TokenResponse)
def feishu_callback(code: str, db: Session = Depends(get_session)) -> TokenResponse:
    """Exchange Feishu auth code for our JWT.

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
        raise HTTPException(status_code=401, detail=f"sso failed: {e}") from e

    db.commit()
    token, ttl = issue_jwt(sub=str(authed.user_id), name=authed.name, role=authed.role)
    return TokenResponse(
        access_token=token,
        expires_in=ttl,
        user_id=authed.user_id,
        feishu_uid=authed.feishu_uid,
        name=authed.name,
        role=authed.role,
    )


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
