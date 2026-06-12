"""Dedup proposal executor (D4 第①段) — act on `dedup_link` audit rows.

Mirrors split.py's playbook (audit row → supervisor queue → execute/dismiss):

    execute  — link the duplicate ticket onto the ORIGINAL's hub_issue:
               subject.hub_issue_id = target.hub_issue_id,
               hub.occurrence_count += 1, hub.last_seen_at = now,
               ticket_hub_issue_history (human_confirmed) + decision
               proposal['materialized'] audit block. NO LLM.
    dismiss  — flip the decision to 'reverted' (stays auditable).

Guards keep the action safe:
    - target ticket must already have a hub_issue (otherwise 先对目标
      create-hub-issue — we never auto-graduate here)
    - subject must still be a live Raw ticket without its own hub_issue
      (already-linked tickets go through /supervisor/relink instead)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket, TicketHubIssueHistory

logger = get_logger(__name__)


class DedupExecuteError(Exception):
    """Proposal can't be executed/dismissed; message is supervisor-facing."""


@dataclass(slots=True, frozen=True)
class DedupExecuteResult:
    decision_id: int
    ticket_id: int
    duplicate_of_ticket_id: int
    hub_issue_id: int
    hub_issue_short_code: str


def list_pending_dedup_proposals(
    db: Session, *, limit: int = 50
) -> list[tuple[AgentDecision, Ticket, Ticket | None]]:
    """Non-reverted, not-yet-materialized dedup_link proposals whose subject
    ticket is still a live Raw without a hub_issue. Returns
    (decision, subject_ticket, target_ticket-or-None)."""
    rows = (
        db.query(AgentDecision, Ticket)
        .join(Ticket, Ticket.id == AgentDecision.subject_id)
        .filter(
            AgentDecision.decision_type == "dedup_link",
            AgentDecision.subject_type == "ticket",
            AgentDecision.status == "executed",
            Ticket.deleted_at.is_(None),
            Ticket.type == "Raw",
            Ticket.hub_issue_id.is_(None),
        )
        .order_by(AgentDecision.id.desc())
        .limit(limit * 2)  # headroom: some rows drop in the Python filter
        .all()
    )
    out: list[tuple[AgentDecision, Ticket, Ticket | None]] = []
    for d, t in rows:
        if isinstance(d.proposal.get("materialized"), dict):
            continue
        target_id = d.proposal.get("duplicate_of_ticket_id")
        target = db.get(Ticket, int(target_id)) if target_id else None
        if target is not None and target.deleted_at is not None:
            target = None
        out.append((d, t, target))
        if len(out) >= limit:
            break
    return out


def dismiss_dedup_proposal(
    decision_id: int,
    *,
    dismissed_by: str,
    reason: str | None = None,
    db: Session,
) -> int:
    decision = db.get(AgentDecision, decision_id)
    if decision is None:
        raise DedupExecuteError(f"decision {decision_id} not found")
    if decision.decision_type != "dedup_link":
        raise DedupExecuteError(
            f"decision {decision_id} is {decision.decision_type!r}, not dedup_link"
        )
    if decision.status == "reverted":
        raise DedupExecuteError(f"decision {decision_id} already reverted")
    if isinstance(decision.proposal.get("materialized"), dict):
        raise DedupExecuteError(f"decision {decision_id} already materialized")

    decision.status = "reverted"
    decision.reverted_at = datetime.now(UTC)
    decision.reverted_by = dismissed_by
    decision.revert_reason = reason or "dismissed by supervisor (not a duplicate)"
    db.commit()
    logger.info(
        "dedup_proposal_dismissed",
        decision_id=decision.id,
        ticket_id=decision.subject_id,
        dismissed_by=dismissed_by,
    )
    return decision.id


def execute_dedup(
    decision_id: int,
    *,
    executed_by: str,
    db: Session,
) -> DedupExecuteResult:
    """Link the duplicate ticket onto the original's hub_issue. Commits."""
    decision = db.get(AgentDecision, decision_id)
    if decision is None:
        raise DedupExecuteError(f"decision {decision_id} not found")
    if decision.decision_type != "dedup_link":
        raise DedupExecuteError(
            f"decision {decision_id} is {decision.decision_type!r}, not dedup_link"
        )
    if decision.status != "executed" or decision.reverted_at is not None:
        raise DedupExecuteError(f"decision {decision_id} already reverted")
    if isinstance(decision.proposal.get("materialized"), dict):
        raise DedupExecuteError(f"decision {decision_id} already materialized")

    subject = db.get(Ticket, decision.subject_id)
    if subject is None or subject.deleted_at is not None:
        raise DedupExecuteError(f"ticket {decision.subject_id} not found")
    if subject.hub_issue_id is not None:
        raise DedupExecuteError(
            f"ticket {subject.id} already linked to hub_issue {subject.hub_issue_id} — "
            "use /supervisor/relink to change it"
        )

    target_id = decision.proposal.get("duplicate_of_ticket_id")
    if not target_id:
        raise DedupExecuteError(f"decision {decision_id} has no duplicate_of_ticket_id")
    target = db.get(Ticket, int(target_id))
    if target is None or target.deleted_at is not None:
        raise DedupExecuteError(f"duplicate target ticket {target_id} not found")
    if target.hub_issue_id is None:
        raise DedupExecuteError(
            f"目标工单 {target.short_code} 尚未关联 hub_issue — 先对目标执行 "
            "create-hub-issue 再采纳合并"
        )
    hub = db.get(HubIssue, target.hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise DedupExecuteError(f"hub_issue {target.hub_issue_id} not found")

    now = datetime.now(UTC)
    subject.hub_issue_id = hub.id
    hub.occurrence_count += 1
    hub.last_seen_at = now
    db.add(
        TicketHubIssueHistory(
            ticket_id=subject.id,
            hub_issue_id=hub.id,
            change_reason=(
                f"dedup merge by {executed_by}: duplicate of {target.short_code} "
                f"(decision #{decision.id})"
            ),
            human_confirmed=executed_by.startswith("user:"),
        )
    )
    decision.proposal = {
        **decision.proposal,
        "materialized": {
            "at": now.isoformat(),
            "by": executed_by,
            "hub_issue_id": hub.id,
            "target_ticket_id": target.id,
        },
    }
    db.commit()
    logger.info(
        "dedup_executed",
        decision_id=decision.id,
        ticket_id=subject.id,
        duplicate_of_ticket_id=target.id,
        hub_issue_id=hub.id,
        executed_by=executed_by,
    )
    return DedupExecuteResult(
        decision_id=decision.id,
        ticket_id=subject.id,
        duplicate_of_ticket_id=target.id,
        hub_issue_id=hub.id,
        hub_issue_short_code=hub.short_code,
    )
