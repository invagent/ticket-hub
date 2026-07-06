"""Split executor (D3-D 闭环) — materialize a `split_ticket` proposal into
Child tickets. NO LLM involved.

The semantic work (deciding how to split, writing sub-issue titles/summaries)
was already done by conflict_detect (LLM) and persisted in
agent_decisions.proposal.sub_issues. This module mechanically materializes
that proposal under the data-model constraints:

    Child rows (ck_tickets_type_fields):
        source_code = NULL, source_ticket_id = NULL
        internal_split_id = '{parent.short_code}-C{n}'  (deterministic, unique)
        parent_ticket_id = parent.id
    Parent row: type Raw→Parent, status→'split', children_ticket_ids=[...]

Each child is re-routed through the rule-based Router (no LLM) — that is the
executor's real value: sub-problems land with their own owners.

Scope notes:
    - Only consumes decision_type='split_ticket' (content split). The
      router's `multi_match` (one problem, several owning teams) is a
      DIFFERENT situation — ownership ambiguity for the supervisor to
      resolve, never a child-ticket split.
    - Child tickets do NOT re-run conflict_detect (no recursive splitting).
      classify IS re-run on children — orchestrated by the caller
      (webhooks.run_post_ingest_agents / supervisor endpoint), not here,
      so this module stays LLM-free and synchronous.

Trigger model (split_auto_* settings):
    confidence >= split_auto_confidence AND split_auto_enabled → auto-run
    after conflict_detect; otherwise the proposal waits for a supervisor
    to POST /api/supervisor/execute-split. Both paths call execute_split().

Revert (supervisor): soft-delete children (refused if any child has made
progress), restore parent to Raw + its pre-split status, flip the
agent_decisions row to status='reverted'.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.routing.router import Router, RouteRequest
from app.services.system_settings import get_default_pool_user_id

logger = get_logger(__name__)

# 子单可继承的类型（Complaint 不进拆分子单——投诉停 ticket 层转人工）
_VALID_CHILD_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task"})


class SplitError(Exception):
    """Proposal can't be executed / reverted; message is supervisor-facing."""


@dataclass(slots=True, frozen=True)
class SplitResult:
    decision_id: int
    parent_ticket_id: int
    child_ticket_ids: list[int]


@dataclass(slots=True, frozen=True)
class RevertSplitResult:
    decision_id: int
    parent_ticket_id: int
    deleted_child_ids: list[int]


def list_pending_split_proposals(
    db: Session, *, limit: int = 50
) -> list[tuple[AgentDecision, Ticket]]:
    """All non-reverted, not-yet-materialized split_ticket proposals whose
    parent is still a live Raw ticket — the supervisor work-bench queue.

    `materialized` lives inside the JSON proposal, so the final filter runs
    in Python (cross-DB JSON predicates aren't worth it at this volume).
    """
    rows = (
        db.query(AgentDecision, Ticket)
        .join(Ticket, Ticket.id == AgentDecision.subject_id)
        .filter(
            AgentDecision.decision_type == "split_ticket",
            AgentDecision.subject_type == "ticket",
            AgentDecision.status == "executed",
            Ticket.type == "Raw",
            Ticket.deleted_at.is_(None),
        )
        .order_by(AgentDecision.id.desc())
        .limit(limit * 2)  # headroom: some rows drop in the Python filter
        .all()
    )
    pending = [(d, t) for d, t in rows if not isinstance(d.proposal.get("materialized"), dict)]
    return pending[:limit]


def dismiss_split_proposal(
    decision_id: int,
    *,
    dismissed_by: str,
    reason: str | None = None,
    db: Session,
) -> int:
    """Supervisor declines an unmaterialized proposal: flip the decision to
    'reverted' so it leaves the pending queue but stays auditable.

    Materialized splits must go through revert_split (which restores tickets).
    """
    decision = db.get(AgentDecision, decision_id)
    if decision is None:
        raise SplitError(f"decision {decision_id} not found")
    if decision.decision_type != "split_ticket":
        raise SplitError(f"decision {decision_id} is {decision.decision_type!r}, not split_ticket")
    if decision.status == "reverted":
        raise SplitError(f"decision {decision_id} already reverted")
    if isinstance(decision.proposal.get("materialized"), dict):
        raise SplitError(f"decision {decision_id} already materialized — use revert-split instead")

    decision.status = "reverted"
    decision.reverted_at = datetime.now(UTC)
    decision.reverted_by = dismissed_by
    decision.revert_reason = reason or "dismissed by supervisor (proposal declined)"
    db.commit()
    logger.info(
        "split_proposal_dismissed",
        decision_id=decision.id,
        ticket_id=decision.subject_id,
        dismissed_by=dismissed_by,
    )
    return decision.id


def find_pending_split_decision(db: Session, ticket_id: int) -> AgentDecision | None:
    """Latest non-reverted split_ticket proposal whose parent is still Raw
    (i.e. not yet materialized)."""
    decision = (
        db.query(AgentDecision)
        .filter_by(
            decision_type="split_ticket",
            subject_type="ticket",
            subject_id=ticket_id,
            status="executed",
        )
        .order_by(AgentDecision.id.desc())
        .first()
    )
    if decision is None:
        return None
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.type != "Raw":
        return None
    return decision


def execute_split(
    decision_id: int,
    *,
    executed_by: str,
    db: Session,
) -> SplitResult:
    """Materialize one split_ticket proposal. Commits on success.

    Raises SplitError on any validation failure (caller decides whether to
    surface it to a supervisor or just log it).
    Idempotency guard: parent must still be type='Raw'.
    """
    decision = db.get(AgentDecision, decision_id)
    if decision is None:
        raise SplitError(f"decision {decision_id} not found")
    if decision.decision_type != "split_ticket":
        raise SplitError(f"decision {decision_id} is {decision.decision_type!r}, not split_ticket")
    if decision.status != "executed" or decision.reverted_at is not None:
        raise SplitError(f"decision {decision_id} already reverted")

    sub_issues_raw = decision.proposal.get("sub_issues")
    if not isinstance(sub_issues_raw, list) or len(sub_issues_raw) < 2:
        raise SplitError(f"decision {decision_id} proposal has <2 sub_issues")

    parent = db.get(Ticket, decision.subject_id)
    if parent is None or parent.deleted_at is not None:
        raise SplitError(f"parent ticket {decision.subject_id} not found")
    if parent.type != "Raw":
        # Already materialized (or this is itself a child) — idempotent skip.
        raise SplitError(f"ticket {parent.id} is type={parent.type!r}, expected Raw")

    tickets = TicketRepository(db)
    history = StatusHistoryRepository(db)
    router = Router(db, default_pool_user_id=get_default_pool_user_id(db))

    child_ids: list[int] = []
    from decimal import Decimal

    # ADR-0016 P2c：triage 提案的 sub_issue 带 sub_type → 子单直接继承类型/置信度
    # （原子单不再分类）。旧 conflict_detect 提案无 sub_type → 留 None，由 caller
    # 兜底跑 classify（向后兼容 SIT 存量待拆分提案）。
    parent_conf = decision.proposal.get("confidence")
    for i, sub in enumerate(sub_issues_raw, start=1):
        title = str(sub.get("title") or "").strip()
        if not title:
            raise SplitError(f"decision {decision_id} sub_issue #{i} has empty title")
        summary = str(sub.get("summary") or "")
        sub_type = sub.get("sub_type")
        inherit_type = sub_type if sub_type in _VALID_CHILD_TYPES else None

        child = Ticket(
            short_code=tickets.next_short_code(),
            # ck_tickets_type_fields: Child must drop source provenance
            source_code=None,
            source_ticket_id=None,
            internal_split_id=f"{parent.short_code}-C{i}",
            type="Child",
            parent_ticket_id=parent.id,
            status="received",
            # inherited context (same customer, same product surface)
            customer_identity_id=parent.customer_identity_id,
            product_line_code=parent.product_line_code,
            module=parent.module,
            feature=parent.feature,
            reporter=parent.reporter,
            # content comes from the LLM-authored sub-issue, NOT from
            # slicing the original text (parent keeps the full body)
            title=title,
            body=summary or None,
            predicted_type=inherit_type,
            predicted_confidence=(
                Decimal(f"{float(parent_conf):.2f}")
                if inherit_type and isinstance(parent_conf, int | float)
                else None
            ),
            classified_at=datetime.now(UTC) if inherit_type else None,
        )
        db.add(child)
        db.flush()  # need child.id for routing + history

        route = router.route(
            RouteRequest(
                ticket_id=child.id,
                source_code=parent.source_code or "internal",
                product_line_code=child.product_line_code,
                raw_module=child.module,
                raw_feature=child.feature,
            )
        )
        if (route.decision == "assigned" and len(route.assigned_user_ids) == 1) or (
            route.decision == "default_pool" and route.assigned_user_ids
        ):
            child.assigned_user_id = route.assigned_user_ids[0]

        history.record(
            entity_type="ticket",
            entity_id=child.id,
            from_status=None,
            to_status="received",
            changed_by=executed_by,
            reason=f"split from {parent.short_code} (decision #{decision.id})",
            metadata={
                "parent_ticket_id": parent.id,
                "internal_split_id": child.internal_split_id,
                "routing_decision": route.decision,
                "matched_scope": route.matched_scope,
                "rationale": route.rationale,
            },
        )
        child_ids.append(child.id)

    prev_status = parent.status
    parent.type = "Parent"
    parent.status = "split"
    parent.children_ticket_ids = child_ids

    history.record(
        entity_type="ticket",
        entity_id=parent.id,
        from_status=prev_status,
        to_status="split",
        changed_by=executed_by,
        reason=f"materialized split decision #{decision.id} into {len(child_ids)} children",
        metadata={"child_ticket_ids": child_ids},
    )

    # Audit the materialization on the decision row itself (status stays
    # 'executed'; CHECK only allows executed/reverted). prev_status is what
    # revert restores.
    decision.proposal = {
        **decision.proposal,
        "materialized": {
            "at": datetime.now(UTC).isoformat(),
            "by": executed_by,
            "child_ticket_ids": child_ids,
            "parent_prev_status": prev_status,
        },
    }

    db.commit()
    logger.info(
        "split_executed",
        decision_id=decision.id,
        parent_ticket_id=parent.id,
        child_ticket_ids=child_ids,
        executed_by=executed_by,
    )
    return SplitResult(
        decision_id=decision.id,
        parent_ticket_id=parent.id,
        child_ticket_ids=child_ids,
    )


def execute_split_for_ticket(
    ticket_id: int,
    *,
    executed_by: str,
    db: Session | None = None,
) -> SplitResult | None:
    """Auto-path convenience (BackgroundTask): find the pending proposal for
    a ticket and execute it. Returns None (logged) instead of raising — the
    auto path must never crash the post-ingest pipeline.
    """
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None
    try:
        decision = find_pending_split_decision(db, ticket_id)
        if decision is None:
            logger.warning("split_auto_no_pending_decision", ticket_id=ticket_id)
            return None
        return execute_split(decision.id, executed_by=executed_by, db=db)
    except SplitError as e:
        db.rollback()
        logger.warning("split_auto_failed", ticket_id=ticket_id, error=str(e))
        return None
    except Exception:
        db.rollback()
        logger.exception("split_auto_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()


def revert_split(
    decision_id: int,
    *,
    reverted_by: str,
    reason: str | None = None,
    db: Session,
) -> RevertSplitResult:
    """Undo a materialized split. Commits on success.

    Refuses (SplitError) if any child has made progress — status moved past
    'received' — because deleting work-in-progress would lose state; the
    supervisor handles those by hand.
    """
    decision = db.get(AgentDecision, decision_id)
    if decision is None:
        raise SplitError(f"decision {decision_id} not found")
    if decision.decision_type != "split_ticket":
        raise SplitError(f"decision {decision_id} is {decision.decision_type!r}, not split_ticket")
    if decision.status == "reverted":
        raise SplitError(f"decision {decision_id} already reverted")

    materialized = decision.proposal.get("materialized")
    if not isinstance(materialized, dict):
        raise SplitError(f"decision {decision_id} was never materialized — nothing to revert")

    parent = db.get(Ticket, decision.subject_id)
    if parent is None:
        raise SplitError(f"parent ticket {decision.subject_id} not found")
    if parent.type != "Parent":
        raise SplitError(f"ticket {parent.id} is type={parent.type!r}, expected Parent")

    child_ids_any: Any = materialized.get("child_ticket_ids") or []
    child_ids: list[int] = [int(c) for c in child_ids_any]
    children: list[Ticket] = [
        t for cid in child_ids if (t := db.get(Ticket, cid)) is not None and t.deleted_at is None
    ]

    # Progress guard: routing-assignment at creation is NOT progress;
    # any status transition past 'received' is.
    busy = [c.id for c in children if c.status != "received"]
    if busy:
        raise SplitError(
            f"children {busy} already in progress (status != received) — revert refused, "
            "handle manually"
        )

    now = datetime.now(UTC)
    history = StatusHistoryRepository(db)
    for c in children:
        c.deleted_at = now
        history.record(
            entity_type="ticket",
            entity_id=c.id,
            from_status=c.status,
            to_status="deleted",
            changed_by=reverted_by,
            reason=f"revert split decision #{decision.id}",
            metadata={"parent_ticket_id": parent.id},
        )

    prev_status = str(materialized.get("parent_prev_status") or "received")
    parent.type = "Raw"
    parent.status = prev_status
    parent.children_ticket_ids = None

    decision.status = "reverted"
    decision.reverted_at = now
    decision.reverted_by = reverted_by
    decision.revert_reason = reason

    history.record(
        entity_type="ticket",
        entity_id=parent.id,
        from_status="split",
        to_status=prev_status,
        changed_by=reverted_by,
        reason=f"revert split decision #{decision.id}",
        metadata={"deleted_child_ids": [c.id for c in children]},
    )

    db.commit()
    logger.info(
        "split_reverted",
        decision_id=decision.id,
        parent_ticket_id=parent.id,
        deleted_child_ids=[c.id for c in children],
        reverted_by=reverted_by,
    )
    return RevertSplitResult(
        decision_id=decision.id,
        parent_ticket_id=parent.id,
        deleted_child_ids=[c.id for c in children],
    )
