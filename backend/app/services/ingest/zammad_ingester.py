"""ZammadIngester — parallel of KSMIngester / ZhichiIngester for Zammad webhooks.

Field mapping (Zammad v6.x → internal model):
  ticket.id          → source_ticket_id (str)
  ticket.number      → short_code reference (stored in source_payload)
  ticket.title       → title
  article.body       → body
  ticket.group       → module  (Zammad group = team owning the ticket)
  ticket.tags        → feature (first matching feature-scope tag, if any)
  ticket.product_line_code → product_line_code  (custom Zammad field, optional)
  ticket.customer.email   → email
  ticket.customer.phone   → mobile
  ticket.customer.name    → raw_name
  ticket.erp_uid          → erp_uid (custom Zammad field, optional)

Idempotency: dedupe by (source='zammad', source_ticket_id=str(ticket.id)).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from adapters.zammad.types import ZammadTicket
from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.dispatch import dispatch_handler
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    customer_id: int
    customer_identity_id: int
    routing_decision: str
    assigned_user_ids: list[int] = field(default_factory=list)
    deduped: bool = False


class IngestError(Exception):
    """Validation failure — 400 to caller."""


class ZammadIngester:
    """Processes a single Zammad webhook payload end-to-end."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        zt = self._parse(payload)
        source_ticket_id = str(zt.id)

        existing = self._tickets.find_by_source("zammad", source_ticket_id)
        if existing is not None:
            logger.info(
                "zammad_ingest_dedup",
                zammad_id=zt.id,
                existing_ticket_id=existing.id,
            )
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                customer_id=self._customer_id_of(existing),
                customer_identity_id=existing.customer_identity_id or 0,
                routing_decision="dedup",
                assigned_user_ids=(
                    [existing.assigned_user_id] if existing.assigned_user_id else []
                ),
                deduped=True,
            )

        identity_input = IdentityInput(
            source_code="zammad",
            source_user_id=str(zt.customer.id) if zt.customer.id else None,
            erp_uid=zt.erp_uid,
            email=zt.customer.email or None,
            mobile=zt.customer.phone or None,
            raw_name=zt.customer.name or None,
            raw_payload=payload,
        )
        resolve = self._resolver.resolve(identity_input)

        # Ensure product_line and module rows exist (auto-create if new)
        upsert_catalog(
            self._db,
            product_line_code=zt.product_line_code,
            module=zt.group or None,
        )

        short_code = self._tickets.next_short_code()
        ticket = Ticket(
            short_code=short_code,
            source_code="zammad",
            source_ticket_id=source_ticket_id,
            type="Raw",
            status="received",
            source_payload=payload,
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=safe_product_line_code(self._db, zt.product_line_code),
            module=zt.group or None,
            feature=self._pick_feature(zt.tags),
            title=zt.title or None,
            body=zt.article.body or None,
            reporter={
                "name": zt.customer.name,
                "email": zt.customer.email,
                "mobile": zt.customer.phone,
                "source_user_id": str(zt.customer.id) if zt.customer.id else None,
                "zammad_number": zt.number,
            },
        )
        self._tickets.add(ticket)
        self._db.flush()  # dispatch_handler.add_log 需要 ticket.id 已落库

        dr = dispatch_handler(self._db, ticket)
        if dr.user_id is not None:
            ticket.assigned_user_id = dr.user_id
            ticket.handler_user_id = dr.user_id  # 处理人初始=责任人
        dispatch_decision = "assigned" if dr.user_id is not None else "no_match"

        self._db.flush()

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"zammad webhook: {zt.id} (#{zt.number})",
            metadata={
                "source": "zammad",
                "zammad_state": zt.state,
                "zammad_priority": zt.priority,
                "routing_decision": dispatch_decision,
                "matched_scope": "dispatch_rule" if dr.rule_id is not None else "none",
                "rationale": dr.reason,
            },
        )

        logger.info(
            "zammad_ingest_committed",
            ticket_id=ticket.id,
            short_code=short_code,
            zammad_id=zt.id,
            customer_id=resolve.customer_id,
            routing_decision=dispatch_decision,
        )
        return IngestResult(
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            customer_identity_id=resolve.customer_identity_id,
            routing_decision=dispatch_decision,
            assigned_user_ids=[dr.user_id] if dr.user_id is not None else [],
            deduped=False,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(payload: dict[str, Any]) -> ZammadTicket:
        ticket = payload.get("ticket") or {}
        if not ticket.get("id"):
            raise IngestError("missing ticket.id in Zammad payload")
        return ZammadTicket.from_payload(payload)

    @staticmethod
    def _pick_feature(tags: list[str]) -> str | None:
        """Use the first tag as a feature hint (D3 will use LLM for classification)."""
        return tags[0] if tags else None

    def _customer_id_of(self, ticket: Ticket) -> int:
        if ticket.customer_identity_id is None:
            return 0
        from app.models import CustomerIdentity

        ident = self._db.get(CustomerIdentity, ticket.customer_identity_id)
        return ident.customer_id if ident else 0
