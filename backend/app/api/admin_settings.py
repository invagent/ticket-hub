"""Admin settings API — runtime system configuration.

  GET  /api/admin/settings/default-pool-user  — get current default pool user
  PUT  /api/admin/settings/default-pool-user  — set default pool user

Requires role IN ('supervisor', 'admin').
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_supervisor
from app.core.logging import get_logger
from app.db import get_session
from app.models import SystemSetting, User

router = APIRouter()
logger = get_logger(__name__)

_KEY = "default_pool_user_id"


class DefaultPoolUserOut(BaseModel):
    user_id: int | None
    user_name: str | None


class DefaultPoolUserIn(BaseModel):
    user_id: int | None


def _build_out(db: Session) -> DefaultPoolUserOut:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == _KEY)).scalar_one_or_none()
    if row is None or row.value is None:
        return DefaultPoolUserOut(user_id=None, user_name=None)
    try:
        uid = int(row.value)
    except (ValueError, TypeError):
        return DefaultPoolUserOut(user_id=None, user_name=None)
    user = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
    return DefaultPoolUserOut(
        user_id=uid,
        user_name=user.name if user else None,
    )


@router.get("/default-pool-user", response_model=DefaultPoolUserOut)
def get_default_pool_user(
    _: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DefaultPoolUserOut:
    return _build_out(db)


@router.put("/default-pool-user", response_model=DefaultPoolUserOut)
def put_default_pool_user(
    body: DefaultPoolUserIn,
    current_user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DefaultPoolUserOut:
    if body.user_id is not None:
        user = db.execute(
            select(User).where(User.id == body.user_id, User.is_active.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=422, detail="user not found or inactive")

    row = db.execute(select(SystemSetting).where(SystemSetting.key == _KEY)).scalar_one_or_none()

    new_value = str(body.user_id) if body.user_id is not None else None
    if row is None:
        db.add(SystemSetting(key=_KEY, value=new_value, updated_by=current_user.user_id))
    else:
        row.value = new_value
        row.updated_by = current_user.user_id

    db.commit()
    logger.info("system_setting_updated", key=_KEY, value=new_value, by=current_user.user_id)
    return _build_out(db)
