"""Knowledge-feedback service — thin glue over the AI 客服 adapter.

Keeps two responsibilities out of the API layer so both stay testable:
  - build_client(): feature-gate + construct AiCsClient from settings
  - load_escalation_context(): pull the golden triple off an ai_cs ticket so
    the reflect UI can show the original question + AI answer, and replay can
    reuse the original session_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsClient, AiCsConfig
from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

_AI_CS_SOURCE = "ai_cs"


class KnowledgeFeedbackDisabledError(Exception):
    """Feature off or AI 客服 credentials missing — caller maps to HTTP 503."""


def build_client(settings: Any) -> AiCsClient:
    """Construct an AiCsClient, or raise KnowledgeFeedbackDisabledError if the
    feature is off / not configured. Caller owns closing the client."""
    if not getattr(settings, "knowledge_feedback_enabled", False):
        raise KnowledgeFeedbackDisabledError("知识反哺未启用（knowledge_feedback_enabled=false）")
    if not getattr(settings, "ai_cs_app_id", "") or not getattr(settings, "ai_cs_app_key", ""):
        raise KnowledgeFeedbackDisabledError("AI 客服 appid/app_key 未配置")
    return AiCsClient(AiCsConfig.from_settings(settings))


@dataclass(slots=True, frozen=True)
class EscalationContext:
    """The golden triple carried by an ai_cs escalation ticket."""

    ticket_id: int
    session_id: str | None  # ticket.source_ticket_id — replay can reuse this
    original_question: str
    ai_answer: str
    dissatisfaction: str


def load_escalation_context(db: Session, ticket_id: int) -> EscalationContext | None:
    """Return the escalation golden triple for a ticket, or None if the ticket
    is not an AI 客服 escalation (no reflect context to show)."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        return None
    if ticket.source_code != _AI_CS_SOURCE:
        return None
    ai = (ticket.source_payload or {}).get("ai_cs") or {}
    return EscalationContext(
        ticket_id=ticket.id,
        session_id=ticket.source_ticket_id,
        original_question=str(ai.get("original_question") or ticket.body or ""),
        ai_answer=str(ai.get("ai_answer") or ""),
        dissatisfaction=str(ai.get("dissatisfaction") or ""),
    )


def record_publish_audit(
    db: Session,
    *,
    ticket_id: int,
    skill_name: str,
    version: str,
    operator: str,
) -> None:
    """Tie a published skill revision back to the escalation ticket that
    triggered it (audit-only status_history row; status unchanged)."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        return
    StatusHistoryRepository(db).record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=ticket.status,
        to_status=ticket.status,  # audit event — no status transition
        changed_by=operator,
        reason=f"知识反哺：发布 AI 客服 skill {version}",
        metadata={"kind": "knowledge_revision", "skill": skill_name, "version": version},
    )
    logger.info(
        "knowledge_feedback_publish_audit",
        ticket_id=ticket.id,
        skill=skill_name,
        version=version,
    )
