"""JWT auth dependency for protected API endpoints.

Usage:
    from fastapi import Depends
    from app.api.deps.auth import require_user, require_supervisor

    @router.get("/protected")
    def handler(user: AuthedUser = Depends(require_user)) -> ...:
        ...

    @router.post("/admin")
    def admin_only(user: AuthedUser = Depends(require_supervisor)) -> ...:
        ...

JWT comes via `Authorization: Bearer <token>` header.
401 on missing / invalid / expired token.
403 on role mismatch (require_supervisor / require_admin).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from app.config import get_settings


@dataclass(slots=True, frozen=True)
class AuthedUser:
    user_id: int
    name: str
    role: str  # 'member' | 'assignee' | 'supervisor' | 'admin'


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization Bearer token",
        )
    return auth[len("Bearer ") :].strip()


def require_user(request: Request) -> AuthedUser:
    """Verify JWT; return AuthedUser. Use as a FastAPI dependency."""
    settings = get_settings()
    token = _extract_token(request)
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {e}"
        ) from e
    sub = payload.get("sub")
    name = payload.get("name") or ""
    role = payload.get("role") or "member"
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token missing sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token sub not numeric"
        ) from e
    return AuthedUser(user_id=user_id, name=name, role=role)


def require_supervisor(user: AuthedUser = Depends(require_user)) -> AuthedUser:
    if user.role not in ("supervisor", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="supervisor or admin role required",
        )
    return user


def require_admin(user: AuthedUser = Depends(require_user)) -> AuthedUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return user
