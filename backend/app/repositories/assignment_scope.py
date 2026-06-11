"""assignment_scopes_module / _feature lookups + admin CRUD."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeFeature,
    AssignmentScopeHistory,
    AssignmentScopeModule,
    UserPartner,
)


class AssignmentScopeRepository:
    """Read-only routing lookups used by Router (hot path)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def find_user_ids_by_module(self, product_line_code: str, module: str) -> list[int]:
        stmt = select(AssignmentScopeModule.user_id).where(
            AssignmentScopeModule.product_line_code == product_line_code,
            AssignmentScopeModule.module == module,
        )
        return list(self._db.execute(stmt).scalars().all())

    def find_user_ids_by_feature(self, feature: str) -> list[int]:
        stmt = select(AssignmentScopeFeature.user_id).where(
            AssignmentScopeFeature.feature == feature
        )
        return list(self._db.execute(stmt).scalars().all())


class AssignmentScopeAdminRepository:
    """Admin writes — add / delete / list with filters + auto history audit.

    Each add/delete writes one assignment_scope_history row inside the same
    transaction (per spec §4.12.1). Caller commits the transaction.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- module --------------------------------------------------------

    def list_modules(
        self,
        *,
        user_id: int | None = None,
        product_line_code: str | None = None,
        module: str | None = None,
    ) -> list[AssignmentScopeModule]:
        stmt = select(AssignmentScopeModule)
        if user_id is not None:
            stmt = stmt.where(AssignmentScopeModule.user_id == user_id)
        if product_line_code:
            stmt = stmt.where(AssignmentScopeModule.product_line_code == product_line_code)
        if module:
            stmt = stmt.where(AssignmentScopeModule.module == module)
        stmt = stmt.order_by(AssignmentScopeModule.id)
        return list(self._db.execute(stmt).scalars().all())

    def add_module(
        self,
        *,
        user_id: int,
        product_line_code: str,
        module: str,
        changed_by: int,
    ) -> AssignmentScopeModule:
        row = AssignmentScopeModule(
            user_id=user_id, product_line_code=product_line_code, module=module
        )
        self._db.add(row)
        self._db.flush()
        self._write_history(
            scope_type="module",
            user_id=user_id,
            action="add",
            payload={"product_line_code": product_line_code, "module": module},
            changed_by=changed_by,
        )
        return row

    def delete_module(self, *, scope_id: int, changed_by: int) -> AssignmentScopeModule | None:
        row = self._db.get(AssignmentScopeModule, scope_id)
        if row is None:
            return None
        snapshot = {
            "id": row.id,
            "product_line_code": row.product_line_code,
            "module": row.module,
        }
        affected_user = row.user_id
        self._db.delete(row)
        self._db.flush()
        self._write_history(
            scope_type="module",
            user_id=affected_user,
            action="remove",
            payload=snapshot,
            changed_by=changed_by,
        )
        return row

    # ---- feature -------------------------------------------------------

    def list_features(
        self,
        *,
        user_id: int | None = None,
        feature: str | None = None,
    ) -> list[AssignmentScopeFeature]:
        stmt = select(AssignmentScopeFeature)
        if user_id is not None:
            stmt = stmt.where(AssignmentScopeFeature.user_id == user_id)
        if feature:
            stmt = stmt.where(AssignmentScopeFeature.feature == feature)
        stmt = stmt.order_by(AssignmentScopeFeature.id)
        return list(self._db.execute(stmt).scalars().all())

    def add_feature(self, *, user_id: int, feature: str, changed_by: int) -> AssignmentScopeFeature:
        row = AssignmentScopeFeature(user_id=user_id, feature=feature)
        self._db.add(row)
        self._db.flush()
        self._write_history(
            scope_type="feature",
            user_id=user_id,
            action="add",
            payload={"feature": feature},
            changed_by=changed_by,
        )
        return row

    def delete_feature(self, *, scope_id: int, changed_by: int) -> AssignmentScopeFeature | None:
        row = self._db.get(AssignmentScopeFeature, scope_id)
        if row is None:
            return None
        snapshot = {"id": row.id, "feature": row.feature}
        affected_user = row.user_id
        self._db.delete(row)
        self._db.flush()
        self._write_history(
            scope_type="feature",
            user_id=affected_user,
            action="remove",
            payload=snapshot,
            changed_by=changed_by,
        )
        return row

    # ---- history -------------------------------------------------------

    def list_history(
        self,
        *,
        user_id: int | None = None,
        scope_type: str | None = None,
        limit: int = 100,
    ) -> list[AssignmentScopeHistory]:
        stmt = select(AssignmentScopeHistory)
        if user_id is not None:
            stmt = stmt.where(AssignmentScopeHistory.user_id == user_id)
        if scope_type:
            stmt = stmt.where(AssignmentScopeHistory.scope_type == scope_type)
        stmt = stmt.order_by(
            AssignmentScopeHistory.changed_at.desc(),
            AssignmentScopeHistory.id.desc(),
        ).limit(min(limit, 500))
        return list(self._db.execute(stmt).scalars().all())

    def _write_history(
        self,
        *,
        scope_type: str,
        user_id: int,
        action: str,
        payload: dict[str, object],
        changed_by: int,
    ) -> AssignmentScopeHistory:
        row = AssignmentScopeHistory(
            scope_type=scope_type,
            user_id=user_id,
            action=action,
            payload=payload,
            changed_by=changed_by,
        )
        self._db.add(row)
        self._db.flush()
        return row


class UserPartnerRepository:
    """Symmetric partner pairs. Two users in the same pair count as one routing unit."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_partner_ids(self, user_id: int) -> set[int]:
        stmt = select(UserPartner.partner_id).where(UserPartner.user_id == user_id)
        return set(self._db.execute(stmt).scalars().all())

    def group_by_partner(self, user_ids: list[int]) -> list[set[int]]:
        """Collapse a flat list of user_ids into groups (user + partners count once).

        Algorithm: union-find via partner edges. Returns each connected
        component as a set of user_ids. Order is by smallest member id.
        """
        if not user_ids:
            return []

        unique = list(dict.fromkeys(user_ids))  # preserve order, drop dups
        # Build all partner edges among the candidate set
        partners: dict[int, set[int]] = {u: self.get_partner_ids(u) for u in unique}

        parent: dict[int, int] = {u: u for u in unique}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra if ra < rb else rb] = ra if ra > rb else rb

        candidate_set = set(unique)
        for u, mates in partners.items():
            for m in mates:
                if m in candidate_set:
                    union(u, m)

        groups: dict[int, set[int]] = {}
        for u in unique:
            r = find(u)
            groups.setdefault(r, set()).add(u)

        return [groups[k] for k in sorted(groups.keys())]
