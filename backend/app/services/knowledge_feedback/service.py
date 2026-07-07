"""Knowledge-feedback service — thin glue over the AI 客服 adapter.

Keeps two responsibilities out of the API layer so both stay testable:
  - build_client(): feature-gate + construct AiCsClient from settings
  - load_escalation_context(): pull the golden triple off an ai_cs ticket so
    the reflect UI can show the original question + AI answer, and replay can
    reuse the original session_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

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
    """The golden triple + optional feedback-loop extras carried by an ai_cs
    escalation ticket (conversation / cited_knowledge / skills_used are empty
    when the AI 客服 sent the legacy minimal payload)."""

    ticket_id: int
    session_id: str | None  # ticket.source_ticket_id — replay can reuse this
    original_question: str
    ai_answer: str
    dissatisfaction: str
    conversation: list[dict[str, Any]]
    cited_knowledge: list[dict[str, Any]]
    skills_used: list[str]
    # 反思诊断工作台：主管判定（cause/correct_answer）与 LLM 反思推断缓存
    diagnosis: dict[str, Any] | None
    reflection: dict[str, Any] | None


def load_escalation_context(db: Session, ticket_id: int) -> EscalationContext | None:
    """Return the escalation golden triple for a ticket, or None if the ticket
    is not an AI 客服 escalation (no reflect context to show)."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        return None
    if ticket.source_code != _AI_CS_SOURCE:
        return None
    ai = (ticket.source_payload or {}).get("ai_cs") or {}

    def _dict_list(value: Any) -> list[dict[str, Any]]:
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    skills = ai.get("skills_used")
    diagnosis = ai.get("diagnosis")
    reflection = ai.get("reflection")
    return EscalationContext(
        ticket_id=ticket.id,
        session_id=ticket.source_ticket_id,
        original_question=str(ai.get("original_question") or ticket.body or ""),
        ai_answer=str(ai.get("ai_answer") or ""),
        dissatisfaction=str(ai.get("dissatisfaction") or ""),
        conversation=_dict_list(ai.get("conversation")),
        cited_knowledge=_dict_list(ai.get("cited_knowledge")),
        skills_used=[str(s) for s in skills if isinstance(s, str)]
        if isinstance(skills, list)
        else [],
        diagnosis=diagnosis if isinstance(diagnosis, dict) else None,
        reflection=reflection if isinstance(reflection, dict) else None,
    )


_VALID_DIAGNOSIS_CAUSES = frozenset({"skill", "knowledge", "retrieval"})


class NotEscalationError(Exception):
    """Ticket is not an ai_cs escalation — nothing to diagnose."""


def _escalation_ticket(db: Session, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None or ticket.source_code != _AI_CS_SOURCE:
        raise NotEscalationError(f"ticket {ticket_id} is not an ai_cs escalation")
    return ticket


def _set_ai_cs_key(ticket: Ticket, key: str, value: Any) -> None:
    payload = dict(ticket.source_payload or {})
    ai = dict(payload.get("ai_cs") or {})
    if value is None:
        ai.pop(key, None)
    else:
        ai[key] = value
    payload["ai_cs"] = ai
    ticket.source_payload = payload
    flag_modified(ticket, "source_payload")


def save_diagnosis(
    db: Session,
    ticket_id: int,
    *,
    cause: str | None = None,
    causes: list[str] | None = None,
    checklist_done: dict[str, bool] | None = None,
    correct_answer: str | None,
    operator: str,
) -> dict[str, Any] | None:
    """Persist the supervisor's cause verdict + verified correct answer on the
    escalation ticket. Audit via status_history; status itself never changes.

    ADR-0016 P3（决策 6）：病因是集合。`causes` 优先；旧单值 `cause` 兼容
    （包成单元素集合）。两者都空 = 清除诊断。每个病因自动生成一条修复清单项
    `checklist: [{cause, done}]`——更新时保留已勾的 done（除非 checklist_done
    显式改写）；全部 done 即「修复闭环」（skill 病因走 replay 发布，
    knowledge/retrieval 待飞书 KB API 后接调试动作，先人工勾）。
    """
    effective = list(causes) if causes else ([cause] if cause else [])
    seen: list[str] = []
    for c in effective:
        if c not in _VALID_DIAGNOSIS_CAUSES:
            raise ValueError(
                f"invalid cause {c!r}; must be one of {sorted(_VALID_DIAGNOSIS_CAUSES)}"
            )
        if c not in seen:
            seen.append(c)
    effective = seen
    ticket = _escalation_ticket(db, ticket_id)

    # 保留旧清单的 done 状态（主管分批修复，改病因集合不清空进度）
    prev = ((ticket.source_payload or {}).get("ai_cs") or {}).get("diagnosis") or {}
    prev_done = {
        item.get("cause"): bool(item.get("done"))
        for item in (prev.get("checklist") or [])
        if isinstance(item, dict)
    }
    diagnosis: dict[str, Any] | None = None
    if effective or correct_answer:
        checklist = [
            {
                "cause": c,
                "done": (checklist_done or {}).get(c, prev_done.get(c, False)),
            }
            for c in effective
        ]
        diagnosis = {
            "cause": effective[0] if effective else None,  # 主病因（队列过滤/旧消费方）
            "causes": effective,
            "checklist": checklist,
            "resolved": bool(checklist) and all(i["done"] for i in checklist),
            "correct_answer": correct_answer or None,
            "by": operator,
            "at": datetime.now(UTC).isoformat(),
        }
    _set_ai_cs_key(ticket, "diagnosis", diagnosis)
    StatusHistoryRepository(db).record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=ticket.status,
        to_status=ticket.status,  # audit event — no status transition
        changed_by=operator,
        reason=f"反思诊断：病因判定 {'+'.join(effective) or '（清除）'}",
        metadata={"kind": "escalation_diagnosis", "causes": effective},
    )
    logger.info("escalation_diagnosis_saved", ticket_id=ticket.id, causes=effective)
    return diagnosis


def save_reflection(db: Session, ticket_id: int, reflection: dict[str, Any]) -> None:
    """Cache the LLM reflect result on the ticket (overwrites previous run)."""
    ticket = _escalation_ticket(db, ticket_id)
    _set_ai_cs_key(ticket, "reflection", reflection)
    logger.info("escalation_reflection_saved", ticket_id=ticket.id, cause=reflection.get("cause"))


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
