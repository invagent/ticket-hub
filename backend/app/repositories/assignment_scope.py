"""assignment_scopes_module / _feature lookups (used by Router)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeFeature,
    AssignmentScopeModule,
    UserPartner,
)


class AssignmentScopeRepository:
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
