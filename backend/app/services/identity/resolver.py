"""IdentityResolver — match incoming identity payload to a customer.

Algorithm (decision 3, upgrade_plan.md §4.2):

    1. erp_uid       (strongest cross-system key)
    2. mobile        (rarely reused in operational scenarios)
    3. email
    4. (source_code, source_custom_id)   — same-source only
    5. miss → create new customer + customer_identity, resolved_by_key='none'

Strong-key wins: if mobile points at customer_A but email points at customer_B,
we follow the priority order — the highest-priority hit wins. The decision is
recorded with `resolved_by_key`; subsequent supervisor `relink` can revert.

This is a deterministic rule engine (decision 18 modification): no LLM call,
no ambiguity. Conflicts are recorded as agent_decisions in D3; for D1 we just
return the strong-key match and write `resolved_by_key`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Customer, CustomerIdentity
from app.repositories.customer_identity import CustomerIdentityRepository

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class IdentityInput:
    """Payload extracted from an incoming ticket / source-system event."""

    source_code: str  # 'ksm' / 'zhichi' / 'zammad' / 'linear'
    source_user_id: str | None = None
    source_custom_id: str | None = None
    erp_uid: str | None = None
    email: str | None = None
    mobile: str | None = None
    raw_name: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ResolveResult:
    """Outcome of one resolve() call."""

    customer_id: int
    customer_identity_id: int
    resolved_by_key: str  # 'erp_uid' | 'mobile' | 'email' | 'source_custom_id' | 'manual' | 'none'
    created_new: bool

    @property
    def is_new_customer(self) -> bool:
        return self.created_new


class IdentityResolver:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = CustomerIdentityRepository(db)

    def resolve(self, payload: IdentityInput) -> ResolveResult:
        """Match `payload` to a customer; create one if no key matches.

        Side effect on hit: bumps the matched identity's last_seen_at and (if
        the source_user_id was previously NULL) backfills it.

        Side effect on miss: inserts a new Customer + CustomerIdentity inside
        the caller's transaction. Caller is responsible for commit().
        """
        # 1. Same-source exact identity (skip rule engine entirely)
        if payload.source_user_id:
            existing = self._repo.find_by_source_user(payload.source_code, payload.source_user_id)
            if existing:
                self._touch(existing)
                return ResolveResult(
                    customer_id=existing.customer_id,
                    customer_identity_id=existing.id,
                    resolved_by_key=existing.resolved_by_key,
                    created_new=False,
                )

        # 2. Priority-ordered cross-source merge keys
        #    (Each branch returns the strongest key that hits.)
        for key, value, finder in self._priority_keys(payload):
            if not value:
                continue
            match = finder(value)
            if match is None:
                continue
            # Customer matched but no identity for this source yet:
            # create a new identity row pointing at the matched customer.
            new_identity = self._materialize_identity(
                payload, customer_id=match.customer_id, resolved_by_key=key
            )
            return ResolveResult(
                customer_id=match.customer_id,
                customer_identity_id=new_identity.id,
                resolved_by_key=key,
                created_new=False,
            )

        # 3. Total miss → create new customer + identity
        new_customer = Customer(
            display_name=payload.raw_name,
            primary_contact={
                "email": payload.email,
                "mobile": payload.mobile,
                "erp_uid": payload.erp_uid,
            },
        )
        self._db.add(new_customer)
        self._db.flush()
        new_identity = self._materialize_identity(
            payload, customer_id=new_customer.id, resolved_by_key="none"
        )
        logger.info(
            "identity_resolver_new_customer",
            customer_id=new_customer.id,
            source_code=payload.source_code,
        )
        return ResolveResult(
            customer_id=new_customer.id,
            customer_identity_id=new_identity.id,
            resolved_by_key="none",
            created_new=True,
        )

    # ---- internal ------------------------------------------------------

    def _priority_keys(self, payload: IdentityInput):  # type: ignore[no-untyped-def]
        """Iterate (key, value, finder) tuples in decision-3 priority order."""

        def by_source_custom(v: str) -> CustomerIdentity | None:
            return self._repo.find_by_source_custom_id(payload.source_code, v)

        return [
            ("erp_uid", payload.erp_uid, self._repo.find_by_erp_uid),
            ("mobile", payload.mobile, self._repo.find_by_mobile),
            ("email", payload.email, self._repo.find_by_email),
            ("source_custom_id", payload.source_custom_id, by_source_custom),
        ]

    def _materialize_identity(
        self, payload: IdentityInput, *, customer_id: int, resolved_by_key: str
    ) -> CustomerIdentity:
        identity = CustomerIdentity(
            customer_id=customer_id,
            source_code=payload.source_code,
            source_user_id=payload.source_user_id,
            source_custom_id=payload.source_custom_id,
            erp_uid=payload.erp_uid,
            email=payload.email,
            mobile=payload.mobile,
            raw_name=payload.raw_name,
            raw_payload=payload.raw_payload,
            resolved_by_key=resolved_by_key,
        )
        return self._repo.add(identity)

    def _touch(self, identity: CustomerIdentity) -> None:
        from datetime import datetime

        identity.last_seen_at = datetime.now(UTC)
        self._db.flush()
