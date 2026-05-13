"""system_settings.py — runtime config stored in DB with .env fallback."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SystemSetting


def get_default_pool_user_id(db: Session) -> int | None:
    """Return the default pool user ID.

    Priority: DB system_settings > .env DEFAULT_POOL_USER_ID > None.
    """
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == "default_pool_user_id")
    ).scalar_one_or_none()
    if row is not None and row.value is not None:
        try:
            return int(row.value)
        except (ValueError, TypeError):
            return None
    return get_settings().default_pool_user_id
