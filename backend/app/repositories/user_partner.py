"""user_partners CRUD — D2-E.

Partner pairs are symmetric: if A↔B, both `(A,B)` and `(B,A)` rows exist
in `user_partners`. The Router relies on this for partner-group dedup
(see services/routing/router.py).

This repo enforces symmetry: add/remove always touches both directions.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import User, UserPartner


class UserPartnerRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_partners(self, user_id: int) -> list[User]:
        """Return User rows of all current partners of `user_id`."""
        rows = self._db.execute(
            select(User)
            .join(UserPartner, UserPartner.partner_id == User.id)
            .where(UserPartner.user_id == user_id, User.deleted_at.is_(None))
            .order_by(User.id)
        ).scalars()
        return list(rows)

    def add_pair(self, *, user_id: int, partner_id: int) -> bool:
        """Create symmetric partnership. Returns True if newly added,
        False if the pair already exists."""
        if user_id == partner_id:
            raise ValueError("user_id and partner_id cannot be the same")
        existing = self._db.execute(
            select(UserPartner).where(
                or_(
                    and_(UserPartner.user_id == user_id, UserPartner.partner_id == partner_id),
                    and_(UserPartner.user_id == partner_id, UserPartner.partner_id == user_id),
                )
            )
        ).first()
        if existing is not None:
            return False
        self._db.add(UserPartner(user_id=user_id, partner_id=partner_id))
        self._db.add(UserPartner(user_id=partner_id, partner_id=user_id))
        self._db.flush()
        return True

    def remove_pair(self, *, user_id: int, partner_id: int) -> bool:
        """Remove both directions. Returns True if any row was removed."""
        rows = self._db.execute(
            select(UserPartner).where(
                or_(
                    and_(UserPartner.user_id == user_id, UserPartner.partner_id == partner_id),
                    and_(UserPartner.user_id == partner_id, UserPartner.partner_id == user_id),
                )
            )
        ).scalars().all()
        if not rows:
            return False
        for r in rows:
            self._db.delete(r)
        self._db.flush()
        return True
