"""KSMIngester — webhook → ticket pipeline.

Pipeline:
    1. Validate payload has billId + minimal fields
    2. Idempotency: if (source_code='ksm', source_ticket_id=billId) already exists,
       return the existing ticket (no-op write)
    3. Resolve customer identity via IdentityResolver
    4. Insert tickets row (type='Raw', status='received', short_code=TKT-NNNNNN)
    5. Apply Router → set assigned_user_id (skip on multi_match; D3 wires Conflict Detect)
    6. Write status_history (None → 'received')
    7. Return IngestResult

Caller (webhook endpoint) commits the transaction.

D1 scope: only the deterministic flow. Splitting (Conflict Detect → Parent/Child)
lands in D3 along with Agent integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import upsert_catalog
from app.services.routing.router import Router, RouteRequest

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    customer_id: int
    customer_identity_id: int
    routing_decision: str  # 'assigned' | 'multi_match' | 'default_pool'
    assigned_user_ids: list[int] = field(default_factory=list)
    deduped: bool = False  # True if (source, source_ticket_id) already existed


class IngestError(Exception):
    """Validation failure (missing required fields, etc.)."""


class KSMIngester:
    """Stateless. One per request; constructor params injected for testing."""

    def __init__(
        self,
        db: Session,
        *,
        default_pool_user_id: int | None = None,
    ) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)
        self._router = Router(db, default_pool_user_id=default_pool_user_id)

    # ---- public --------------------------------------------------------

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        bill_id = self._require_str(payload, "billId")

        # 1. Idempotency: skip if already ingested
        existing = self._tickets.find_by_source("ksm", bill_id)
        if existing is not None:
            logger.info(
                "ksm_ingest_dedup",
                bill_id=bill_id,
                existing_ticket_id=existing.id,
            )
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                customer_id=(existing.customer_identity_id and self._customer_id_of(existing)) or 0,
                customer_identity_id=existing.customer_identity_id or 0,
                routing_decision="dedup",
                assigned_user_ids=(
                    [existing.assigned_user_id] if existing.assigned_user_id else []
                ),
                deduped=True,
            )

        # 2. Resolve customer identity
        identity_input = self._extract_identity(payload)
        resolve = self._resolver.resolve(identity_input)

        # 3. Ensure product_line + module exist (auto-create if unknown)
        upsert_catalog(
            self._db,
            product_line_code=payload.get("productLineCode") or payload.get("product_line"),
            module=payload.get("moduleName") or payload.get("module"),
        )

        # 4. Create ticket (type=Raw, status=received)
        short_code = self._tickets.next_short_code()
        ticket = Ticket(
            short_code=short_code,
            source_code="ksm",
            source_ticket_id=bill_id,
            type="Raw",
            status="received",
            source_payload=payload,
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=payload.get("productLineCode") or payload.get("product_line"),
            module=payload.get("moduleName") or payload.get("module"),
            feature=payload.get("featureName") or payload.get("feature"),
            title=payload.get("title"),
            body=payload.get("content") or payload.get("description"),
            reporter={
                "name": payload.get("accountName"),
                "email": payload.get("email"),
                "mobile": payload.get("mobile"),
                "tel": payload.get("tel"),
                "source_user_id": payload.get("account"),
            },
        )
        self._tickets.add(ticket)

        # 4. Route
        route = self._router.route(
            RouteRequest(
                ticket_id=ticket.id,
                source_code="ksm",
                product_line_code=ticket.product_line_code,
                raw_module=ticket.module,
                raw_feature=ticket.feature,
                customer_id=resolve.customer_id,
            )
        )
        # Only single-assignee routes to a concrete user.
        # multi_match awaits Conflict Detect Agent (D3) — leave assigned_user_id NULL
        # default_pool: assign if pool is configured + has exactly 1 user
        if (route.decision == "assigned" and len(route.assigned_user_ids) == 1) or (
            route.decision == "default_pool" and route.assigned_user_ids
        ):
            ticket.assigned_user_id = route.assigned_user_ids[0]
        # multi_match → leave None; supervisor or D3 picks up

        self._db.flush()

        # 5. Status history
        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"ksm webhook: {bill_id}",
            metadata={
                "source": "ksm",
                "routing_decision": route.decision,
                "matched_scope": route.matched_scope,
                "rationale": route.rationale,
            },
        )

        logger.info(
            "ksm_ingest_committed",
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            routing_decision=route.decision,
        )

        return IngestResult(
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            customer_identity_id=resolve.customer_identity_id,
            routing_decision=route.decision,
            assigned_user_ids=route.assigned_user_ids,
            deduped=False,
        )

    # ---- internal ------------------------------------------------------

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        v = payload.get(key)
        if not isinstance(v, str) or not v:
            raise IngestError(f"missing or non-string {key}")
        return v

    @staticmethod
    def _extract_identity(payload: dict[str, Any]) -> IdentityInput:
        return IdentityInput(
            source_code="ksm",
            source_user_id=payload.get("account"),
            erp_uid=payload.get("erpUid") or payload.get("erp_uid"),
            email=payload.get("email"),
            mobile=payload.get("mobile"),
            raw_name=payload.get("accountName") or payload.get("linkman"),
            raw_payload=payload,
        )

    def _customer_id_of(self, ticket: Ticket) -> int:
        """Resolve a ticket's customer_id via its customer_identity (for dedup result)."""
        if ticket.customer_identity_id is None:
            return 0
        from app.models import CustomerIdentity

        ident = self._db.get(CustomerIdentity, ticket.customer_identity_id)
        return ident.customer_id if ident else 0
