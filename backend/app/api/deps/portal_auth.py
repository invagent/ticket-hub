"""产品内提单门户鉴权（ADR-0017 D4）。

两层：
1. 租户服务端用 HMAC-SHA256(tenant.hmac_secret, f"{tenant_code}.{external_uid}.{ts}")
   换取**门户 JWT**（`POST /api/portal/auth/token`，见 api/portal.py）。
2. 门户 JWT 带 `aud="portal"`，只能过 `require_portal_user`；员工 JWT（无 aud）走
   `require_user`。python-jose 对「有 aud 但解码方未声明 audience」直接拒绝，反向亦然，
   两套 token 天然互斥，不需要额外的角色判断。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.config import get_settings

PORTAL_AUDIENCE = "portal"


@dataclass(slots=True, frozen=True)
class PortalUser:
    tenant_id: int
    tenant_code: str
    tenant_user_id: int
    external_uid: str
    name: str


def compute_signature(secret: str, tenant_code: str, external_uid: str, ts: int) -> str:
    """租户侧签名算法（文档口径，测试与 SDK 同用）。"""
    msg = f"{tenant_code}.{external_uid}.{ts}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str, tenant_code: str, external_uid: str, ts: int, sign: str, *, skew_seconds: int
) -> bool:
    now = int(datetime.now(UTC).timestamp())
    if abs(now - ts) > skew_seconds:
        return False
    expected = compute_signature(secret, tenant_code, external_uid, ts)
    return hmac.compare_digest(expected, (sign or "").lower())


def issue_portal_jwt(user: PortalUser) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = settings.portal_jwt_ttl_seconds
    payload = {
        "sub": f"portal:{user.tenant_user_id}",
        "aud": PORTAL_AUDIENCE,
        "tenant_id": user.tenant_id,
        "tenant_code": user.tenant_code,
        "tenant_user_id": user.tenant_user_id,
        "external_uid": user.external_uid,
        "name": user.name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), ttl


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization Bearer token",
        )
    return auth[len("Bearer ") :].strip()


def require_portal_user(request: Request) -> PortalUser:
    """校验门户 JWT（aud=portal）。员工 JWT 在这里 401。"""
    settings = get_settings()
    if not settings.portal_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="portal disabled")
    token = _extract_token(request)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=PORTAL_AUDIENCE,
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid portal token: {e}"
        ) from e
    try:
        return PortalUser(
            tenant_id=int(payload["tenant_id"]),
            tenant_code=str(payload["tenant_code"]),
            tenant_user_id=int(payload["tenant_user_id"]),
            external_uid=str(payload["external_uid"]),
            name=str(payload.get("name") or ""),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="portal token missing claims"
        ) from e
