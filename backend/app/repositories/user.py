"""User CRUD repository — D2-E.

Used by:
  - admin /users/* endpoints
  - feishu_sso (upsert on first login) — already does this inline; D2-E moves
    that here as a single source of truth
  - feishu_user_sync (bulk upsert from Feishu contact API)

Soft-delete semantics: `is_active=False` + `deleted_at=now()`. Listing /
get-by-id excludes soft-deleted by default; pass `include_deleted=True`
when you specifically need archived rows (e.g. "revive on next login").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


@dataclass(slots=True)
class UpsertResult:
    """Outcome of a single upsert operation."""

    user: User
    created: bool       # True = new row, False = existing row (possibly updated)
    fields_updated: list[str]  # field names changed on existing row (empty if created)


_UPDATABLE_PROFILE_FIELDS = (
    "name",
    "email",
    "mobile",
    "employee_no",
    "ksm_account",
    "zhichi_agent_id",
    "linear_user_id",
)


class UserRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- read ------------------------------------------------------------

    def list_active(self) -> list[User]:
        return list(
            self._db.execute(
                select(User).where(User.deleted_at.is_(None)).order_by(User.id)
            ).scalars()
        )

    def list_all(self) -> list[User]:
        return list(self._db.execute(select(User).order_by(User.id)).scalars())

    def get(self, user_id: int, *, include_deleted: bool = False) -> User | None:
        u = self._db.get(User, user_id)
        if u is None:
            return None
        if u.deleted_at is not None and not include_deleted:
            return None
        return u

    def get_by_feishu_uid(
        self, feishu_uid: str, *, include_deleted: bool = False
    ) -> User | None:
        stmt = select(User).where(User.feishu_uid == feishu_uid)
        if not include_deleted:
            stmt = stmt.where(User.deleted_at.is_(None))
        return self._db.execute(stmt).scalar_one_or_none()

    # ---- write -----------------------------------------------------------

    def upsert_by_feishu_uid(
        self,
        *,
        feishu_uid: str,
        name: str,
        email: str | None = None,
        mobile: str | None = None,
        employee_no: str | None = None,
        role: str = "member",
        revive_if_deleted: bool = True,
    ) -> UpsertResult:
        """Find by feishu_uid; if exists update profile fields, else create.

        Profile fields (`_UPDATABLE_PROFILE_FIELDS`) are overwritten only when
        the incoming value is non-empty; existing role/is_active/employee_no
        are preserved unless explicitly provided. This matters for SSO where
        we must not downgrade an admin back to member on each login.
        """
        existing = self.get_by_feishu_uid(feishu_uid, include_deleted=True)

        if existing is not None:
            updated: list[str] = []
            if existing.deleted_at is not None and revive_if_deleted:
                existing.deleted_at = None
                existing.is_active = True
                updated.append("deleted_at")
            for field, value in (
                ("name", name),
                ("email", email),
                ("mobile", mobile),
                ("employee_no", employee_no),
            ):
                if value and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    updated.append(field)
            self._db.flush()
            return UpsertResult(user=existing, created=False, fields_updated=updated)

        new = User(
            feishu_uid=feishu_uid,
            name=name,
            email=email,
            mobile=mobile,
            employee_no=employee_no,
            role=role,
            is_active=True,
        )
        self._db.add(new)
        self._db.flush()
        return UpsertResult(user=new, created=True, fields_updated=[])

    def update(
        self,
        user_id: int,
        *,
        patch: dict[str, Any],
    ) -> User | None:
        """Apply a partial update. Returns updated User or None if not found.

        Allowed keys: role / is_active / + _UPDATABLE_PROFILE_FIELDS.
        Unknown keys are silently ignored (caller validates DTO).
        """
        u = self.get(user_id)
        if u is None:
            return None
        for k, v in patch.items():
            if k == "role" and v is not None:
                u.role = v
            elif k == "is_active" and v is not None:
                u.is_active = bool(v)
            elif k in _UPDATABLE_PROFILE_FIELDS:
                # Allow setting to None (clear) or a non-empty string
                setattr(u, k, v)
        self._db.flush()
        return u

    def soft_delete(self, user_id: int) -> User | None:
        """Mark user as deleted; returns the row, or None if not found / already deleted."""
        u = self.get(user_id)
        if u is None:
            return None
        u.is_active = False
        u.deleted_at = datetime.now(UTC)
        self._db.flush()
        return u
