"""Feishu SSO login + JWT issuance.

D0: stub endpoints; full SSO callback wired up in D1 with feishu app
credentials. Decision D19 (v0.5.3): Feishu SSO is the only login entry.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from jose import jwt
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class LoginUrlResponse(BaseModel):
    authorize_url: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.get("/feishu/login", response_model=LoginUrlResponse)
def feishu_login_url() -> LoginUrlResponse:
    """Return the Feishu OAuth2 authorize URL.

    D0: returns the URL deterministically; full state token + nonce
    handling lands in D1.
    """
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
def feishu_callback(code: str) -> TokenResponse:
    """Exchange Feishu auth code for our JWT.

    D0: NOT IMPLEMENTED — returns 501. D1 wires the full flow.
    """
    raise HTTPException(status_code=501, detail="D1: feishu callback pending impl")


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
