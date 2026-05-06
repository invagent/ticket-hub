"""customer_identities lookup operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CustomerIdentity


class CustomerIdentityRepository:
    """Read + write helpers for customer_identities.

    Soft-delete-aware: every method excludes rows with `deleted_at IS NOT NULL`
    unless explicitly asked otherwise.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- exact-match lookups (used by IdentityResolver) ---------------

    def find_by_erp_uid(self, erp_uid: str) -> CustomerIdentity | None:
        return self._first(
            CustomerIdentity.erp_uid == erp_uid,
            CustomerIdentity.deleted_at.is_(None),
        )

    def find_by_mobile(self, mobile: str) -> CustomerIdentity | None:
        return self._first(
            CustomerIdentity.mobile == mobile,
            CustomerIdentity.deleted_at.is_(None),
        )

    def find_by_email(self, email: str) -> CustomerIdentity | None:
        return self._first(
            CustomerIdentity.email == email,
            CustomerIdentity.deleted_at.is_(None),
        )

    def find_by_source_custom_id(
        self, source_code: str, source_custom_id: str
    ) -> CustomerIdentity | None:
        return self._first(
            CustomerIdentity.source_code == source_code,
            CustomerIdentity.source_custom_id == source_custom_id,
            CustomerIdentity.deleted_at.is_(None),
        )

    def find_by_source_user(self, source_code: str, source_user_id: str) -> CustomerIdentity | None:
        return self._first(
            CustomerIdentity.source_code == source_code,
            CustomerIdentity.source_user_id == source_user_id,
            CustomerIdentity.deleted_at.is_(None),
        )

    # ---- write ---------------------------------------------------------

    def add(self, identity: CustomerIdentity) -> CustomerIdentity:
        self._db.add(identity)
        self._db.flush()
        return identity

    # ---- internal ------------------------------------------------------

    def _first(self, *predicates) -> CustomerIdentity | None:  # type: ignore[no-untyped-def]
        stmt = select(CustomerIdentity).where(*predicates).order_by(CustomerIdentity.id).limit(1)
        return self._db.execute(stmt).scalar_one_or_none()
