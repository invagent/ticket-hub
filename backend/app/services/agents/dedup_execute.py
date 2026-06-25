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


def auto_mount_recent_duplicate(ticket_id: int, db: Session | None = None) -> int | None:
    """90 天内语义重复自动挂载（D4 优化 v2 §三需求5）。

    在 dedup 写完 dedup_link 审计后调用：找该工单最新未物化的 dedup_link 提案，
    若目标工单已毕业 hub 且**在 90 天窗口内**且开关开 → 自动 execute_dedup
    （executed_by='agent:dedup_auto'，主管可 relink 纠偏）。返回挂载到的 hub_id 或 None。

    超窗口/目标未毕业/开关关 → 留作主管手动提案（不自动），返回 None。永不抛。
    """
    from datetime import timedelta

    from app.config import get_settings
    from app.db import make_session

    settings = get_settings()
    if not settings.dedup_auto_mount_enabled:
        return None
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None
    try:
        decision = (
            db.query(AgentDecision)
            .filter_by(
                decision_type="dedup_link",
                subject_type="ticket",
                subject_id=ticket_id,
                status="executed",
            )
            .order_by(AgentDecision.id.desc())
            .first()
        )
        if decision is None or isinstance(decision.proposal.get("materialized"), dict):
            return None
        target_id = decision.proposal.get("duplicate_of_ticket_id")
        if not target_id:
            return None
        target = db.get(Ticket, int(target_id))
        if target is None or target.deleted_at is not None or target.hub_issue_id is None:
            return None
        # 90 天窗口：目标工单太老则不自动挂（陈年单不复活），留主管判断
        cutoff = datetime.now(UTC) - timedelta(days=settings.dedup_mount_window_days)
        ref = target.received_at or target.created_at
        if ref is not None and ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)  # SQLite 取回是 naive，PG 是 aware
        if ref is not None and ref < cutoff:
            logger.info(
                "dedup_auto_mount_skip_out_of_window", ticket_id=ticket_id, target_id=target.id
            )
            return None
        result = execute_dedup(decision.id, executed_by="agent:dedup_auto", db=db)
        logger.info(
            "dedup_auto_mounted",
            ticket_id=ticket_id,
            hub_issue_id=result.hub_issue_id,
            target_id=target.id,
        )
        return result.hub_issue_id
    except DedupExecuteError as e:
        db.rollback()
        logger.warning("dedup_auto_mount_failed", ticket_id=ticket_id, error=str(e))
        return None
    except Exception:
        db.rollback()
        logger.exception("dedup_auto_mount_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
