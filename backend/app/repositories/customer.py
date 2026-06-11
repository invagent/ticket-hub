"""Customer search + identity graph queries."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerIdentity


class CustomerRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- read API ------------------------------------------------------

    def get(self, customer_id: int) -> Customer | None:
        c = self._db.get(Customer, customer_id)
        if c is None or c.deleted_at is not None:
            return None
        return c

    def list_identities(self, customer_id: int) -> list[CustomerIdentity]:
        """All non-deleted identities pointing at this customer."""
        stmt = (
            select(CustomerIdentity)
            .where(
                CustomerIdentity.customer_id == customer_id,
                CustomerIdentity.deleted_at.is_(None),
            )
            .order_by(CustomerIdentity.first_seen_at)
        )
        return list(self._db.execute(stmt).scalars().all())

    def search(self, *, q: str, limit: int = 20) -> list[Customer]:
        """Search across customer + identity fields.

        Heuristic: case-sensitive substring match on display_name; exact match
        on email / mobile / erp_uid (these are user-supplied identifiers, exact
        match avoids partial-prefix accidents like '139' matching every mobile).

        Returns up to `limit` distinct customers, deleted_at-aware.
        """
        if not q:
            return []
        like_q = f"%{q}%"

        # Step 1: customer_ids hit by any identity field
        identity_hits = (
            select(CustomerIdentity.customer_id)
            .where(
                CustomerIdentity.deleted_at.is_(None),
                or_(
                    CustomerIdentity.email == q,
                    CustomerIdentity.mobile == q,
                    CustomerIdentity.erp_uid == q,
                    CustomerIdentity.source_custom_id == q,
                    CustomerIdentity.raw_name.like(like_q),
                ),
            )
            .distinct()
        )

        # Step 2: union with customer rows whose display_name matches
        stmt = (
            select(Customer)
            .where(
                Customer.deleted_at.is_(None),
                or_(
                    Customer.display_name.like(like_q),
                    Customer.id.in_(identity_hits),
                ),
            )
            .order_by(Customer.id.desc())
            .limit(min(max(limit, 1), 100))
        )
        return list(self._db.execute(stmt).scalars().all())

    def get_merged_into_chain(self, customer_id: int, *, max_hops: int = 10) -> list[int]:
        """Follow merged_into_customer_id pointers; returns the chain (excluding start).

        Stops on cycle or after max_hops (defensive against bad data).
        """
        chain: list[int] = []
        seen = {customer_id}
        cur = self._db.get(Customer, customer_id)
        for _ in range(max_hops):
            if cur is None or cur.merged_into_customer_id is None:
                break
            nxt_id = cur.merged_into_customer_id
            if nxt_id in seen:
                break
            chain.append(nxt_id)
            seen.add(nxt_id)
            cur = self._db.get(Customer, nxt_id)
        return chain
