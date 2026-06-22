"""EscalationIngester — AI 客服 escalation 工单入库（D4 第③段）.

The AI customer-service system POSTs here when a customer is not satisfied
with its answer. We create a Raw ticket (source='ai_cs') carrying the GOLDEN
TRIPLE in source_payload['ai_cs'] so escalation_classify can read it, plus any
screenshot attachments.

Payload contract (isolated here — adjust `parse_escalation_payload` when the
AI 客服 API format is finalized; everything else stays put):

    {
      "session_id":         "<会话ID>",        # → source_ticket_id (idempotency)
      "original_question":  "<客户原始问题>",
      "ai_answer":          "<AI 客服回答，可多轮拼接>",
      "dissatisfaction":    "<不满反馈/转人工原因>",
      "product_line_code":  "<可选>",
      "module":             "<可选>",
      "customer": {erp_uid?, mobile?, email?, name?, source_user_id?},
      "attachments": [{"url": "...", "filename"?, "mime"?}]   # 截图为主
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Attachment, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import upsert_catalog
from app.services.routing.router import Router, RouteRequest

logger = get_logger(__name__)

_SOURCE = "ai_cs"
_TITLE_MAX = 120


class IngestError(Exception):
    """Validation failure."""


@dataclass(slots=True, frozen=True)
class EscalationParsed:
    session_id: str
    original_question: str
    ai_answer: str
    dissatisfaction: str
    product_line_code: str | None
    module: str | None
    customer: dict[str, Any]
    attachments: list[dict[str, Any]]


def parse_escalation_payload(payload: dict[str, Any]) -> EscalationParsed:
    """Isolated payload mapping. The ONLY place that knows the AI 客服 wire
    format — change here when the real API lands."""
    session_id = payload.get("session_id") or payload.get("sessionId") or payload.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise IngestError("missing session_id")
    question = str(payload.get("original_question") or payload.get("question") or "").strip()
    if not question:
        raise IngestError("missing original_question")
    atts = payload.get("attachments")
    cust = payload.get("customer")
    return EscalationParsed(
        session_id=session_id,
        original_question=question,
        ai_answer=str(payload.get("ai_answer") or payload.get("answer") or "").strip(),
        dissatisfaction=str(
            payload.get("dissatisfaction") or payload.get("feedback") or ""
        ).strip(),
        product_line_code=payload.get("product_line_code") or payload.get("product"),
        module=payload.get("module"),
        customer=cust if isinstance(cust, dict) else {},
        attachments=[a for a in atts if isinstance(a, dict)] if isinstance(atts, list) else [],
    )


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    routing_decision: str
    assigned_user_ids: list[int] = field(default_factory=list)
    attachment_ids: list[int] = field(default_factory=list)
    deduped: bool = False


class EscalationIngester:
    def __init__(self, db: Session, *, default_pool_user_id: int | None = None) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)
        self._router = Router(db, default_pool_user_id=default_pool_user_id)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        p = parse_escalation_payload(payload)

        existing = self._tickets.find_by_source(_SOURCE, p.session_id)
        if existing is not None:
            logger.info("escalation_ingest_dedup", session_id=p.session_id, ticket_id=existing.id)
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                routing_decision="dedup",
                assigned_user_ids=[existing.assigned_user_id] if existing.assigned_user_id else [],
                deduped=True,
            )

        resolve = self._resolver.resolve(
            IdentityInput(
                source_code=_SOURCE,
                source_user_id=p.customer.get("source_user_id") or p.customer.get("erp_uid"),
                erp_uid=p.customer.get("erp_uid"),
                email=p.customer.get("email"),
                mobile=p.customer.get("mobile"),
                raw_name=p.customer.get("name"),
            )
        )
        upsert_catalog(self._db, product_line_code=p.product_line_code, module=p.module)

        ticket = Ticket(
            short_code=self._tickets.next_short_code(),
            source_code=_SOURCE,
            source_ticket_id=p.session_id,
            type="Raw",
            status="received",
            # golden triple lives under ['ai_cs'] for escalation_classify
            source_payload={
                "ai_cs": {
                    "original_question": p.original_question,
                    "ai_answer": p.ai_answer,
                    "dissatisfaction": p.dissatisfaction,
                }
            },
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=p.product_line_code,
            module=p.module,
            title=p.original_question[:_TITLE_MAX],
            body=p.original_question,
            reporter={
                "name": p.customer.get("name"),
                "email": p.customer.get("email"),
                "mobile": p.customer.get("mobile"),
                "source_user_id": p.customer.get("source_user_id"),
            },
        )
        self._tickets.add(ticket)

        route = self._router.route(
            RouteRequest(
                ticket_id=ticket.id,
                source_code=_SOURCE,
                product_line_code=ticket.product_line_code,
                raw_module=ticket.module,
                customer_id=resolve.customer_id,
            )
        )
        if (route.decision == "assigned" and len(route.assigned_user_ids) == 1) or (
            route.decision == "default_pool" and route.assigned_user_ids
        ):
            ticket.assigned_user_id = route.assigned_user_ids[0]
        self._db.flush()

        attachment_ids: list[int] = []
        for a in p.attachments:
            url = a.get("url") or a.get("source_url")
            if not url:
                continue
            att = Attachment(
                ticket_id=ticket.id,
                source_url=str(url),
                filename=a.get("filename"),
                mime=a.get("mime"),
                kind="image",  # 截图为主；非图后续按 mime 细分
                vision_status="pending",
            )
            self._db.add(att)
            self._db.flush()
            attachment_ids.append(att.id)

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"ai_cs escalation: {p.session_id}",
            metadata={
                "source": _SOURCE,
                "routing_decision": route.decision,
                "attachment_count": len(attachment_ids),
            },
        )
        logger.info(
            "escalation_ingest_committed",
            ticket_id=ticket.id,
            short_code=ticket.short_code,
            attachments=len(attachment_ids),
            routing_decision=route.decision,
        )
        return IngestResult(
            ticket_id=ticket.id,
            short_code=ticket.short_code,
            routing_decision=route.decision,
            assigned_user_ids=route.assigned_user_ids,
            attachment_ids=attachment_ids,
            deduped=False,
        )
