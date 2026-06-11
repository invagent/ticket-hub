"""Dashboard metrics — quantitative indicators for D1 verification.

Maps to upgrade_plan.md §12 SLO targets:
  - routing.auto_hit_rate         ≥ 0.95   tickets with assigned_user_id / total
  - supervisor.relink_rate        < 0.10   closed ticket_hub_issue_history rows / linked tickets
  - customer_dedup.match_rate     ≥ 0.90   identities resolved_by_key != 'none' / total identities
  - sla.acknowledgement_rate      ≥ 0.90   acknowledged / total non-escalated notifications

Pure read-side aggregation; computed on the fly. D2+ moves to a materialized
metrics table refreshed by a cron worker (when row counts grow large).

Soft-deleted rows are excluded from all denominators / numerators.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerIdentity,
    HubIssue,
    NotificationLog,
    Ticket,
    TicketHubIssueHistory,
    User,
)


@dataclass(slots=True, frozen=True)
class CountsBlock:
    tickets_total: int
    tickets_active: int  # not done/superseded/rejected/closed
    hub_issues_total: int
    customers_total: int
    users_total: int
    notifications_pending: int


@dataclass(slots=True, frozen=True)
class RoutingBlock:
    """auto_hit_rate = auto_assigned / tickets_total."""

    tickets_total: int
    auto_assigned: int
    auto_hit_rate: float  # 0.0–1.0
    target: str = "≥ 0.95"


@dataclass(slots=True, frozen=True)
class SupervisorBlock:
    """relink_rate proxies "主管调整率" until D3 brings agent_decisions.reverted."""

    linked_tickets: int  # tickets.hub_issue_id IS NOT NULL
    relink_count: int  # closed history rows (effective_to NOT NULL)
    relink_rate: float
    target: str = "< 0.10"


@dataclass(slots=True, frozen=True)
class CustomerDedupBlock:
    """match_rate = how often the resolver hit an existing customer (not 'none')."""

    identities_total: int
    identities_matched: int  # resolved_by_key != 'none'
    match_rate: float
    target: str = "≥ 0.90"


@dataclass(slots=True, frozen=True)
class SLABlock:
    """SLA notification health (D1 proxy; D2 adds %within-threshold)."""

    notifications_total: int
    pending: int  # acked=null AND escalated=null
    acknowledged: int  # acked!=null
    escalated: int  # escalated!=null
    acknowledgement_rate: float  # acked / (acked + escalated + pending) excluding pending
    target: str = "≥ 0.90"


@dataclass(slots=True, frozen=True)
class WebhookIntakeBlock:
    """24h ingest volume by source (D2 monitoring) — surface webhook health."""

    window_hours: int
    by_source: dict[str, int]   # {"ksm": 142, "zhichi": 38, "zammad": 7}
    total: int
    deduped_total: int           # tickets received with `source` but already-existed (proxy)


@dataclass(slots=True, frozen=True)
class DashboardMetrics:
    counts: CountsBlock
    routing: RoutingBlock
    supervisor: SupervisorBlock
    customer_dedup: CustomerDedupBlock
    sla: SLABlock
    webhook_intake: WebhookIntakeBlock


def _from_json(payload: dict) -> DashboardMetrics:
    """Re-hydrate DashboardMetrics from materialized_metrics.metrics_json.

    Keep block constructors in sync with the asdict() in materializer.py;
    schema is owned by `DashboardMetrics`.
    """
    return DashboardMetrics(
        counts=CountsBlock(**payload["counts"]),
        routing=RoutingBlock(**payload["routing"]),
        supervisor=SupervisorBlock(**payload["supervisor"]),
        customer_dedup=CustomerDedupBlock(**payload["customer_dedup"]),
        sla=SLABlock(**payload["sla"]),
        webhook_intake=WebhookIntakeBlock(**payload["webhook_intake"]),
    )


def get_dashboard_metrics(db: Session) -> DashboardMetrics:
    """Public read API used by /api/metrics/dashboard.

    Reads materialized_metrics first (refreshed by Celery beat every 5 min).
    Falls back to on-the-fly compute when the table is empty (fresh DB or
    Celery beat hasn't run yet).
    """
    from app.models import MaterializedMetrics  # avoid import cycle

    row = db.execute(
        select(MaterializedMetrics)
        .where(MaterializedMetrics.slot_key == "latest")
        .limit(1)
    ).scalar_one_or_none()
    if row is not None:
        try:
            return _from_json(row.metrics_json)
        except (KeyError, TypeError) as e:
            # Schema drift between writer and reader — fall through to live compute
            from app.core.logging import get_logger
            get_logger(__name__).warning(
                "dashboard_materialized_payload_invalid", error=str(e)
            )
    return compute_dashboard_metrics(db)


_TICKET_ACTIVE_STATUSES = (
    "received",
    "linked",
    "waiting_reply",
    "waiting_schedule",
    "scheduled",
    "in_progress",
    "code_merged",
    "released",
    "waiting_assign",
    "assigned",
)


def compute_dashboard_metrics(db: Session) -> DashboardMetrics:
    """One round-trip per metric block; fast on small tables, OK on large with indexes.

    All queries soft-delete-aware where applicable.
    """

    # ---- counts ------------------------------------------------------
    tickets_total = db.scalar(select(func.count(Ticket.id)).where(Ticket.deleted_at.is_(None))) or 0
    tickets_active = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.deleted_at.is_(None),
                Ticket.status.in_(_TICKET_ACTIVE_STATUSES),
            )
        )
        or 0
    )
    hub_issues_total = (
        db.scalar(select(func.count(HubIssue.id)).where(HubIssue.deleted_at.is_(None))) or 0
    )
    customers_total = (
        db.scalar(select(func.count(Customer.id)).where(Customer.deleted_at.is_(None))) or 0
    )
    users_total = db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0
    notifications_pending = (
        db.scalar(
            select(func.count(NotificationLog.id)).where(
                NotificationLog.acknowledged_at.is_(None),
                NotificationLog.escalated_at.is_(None),
            )
        )
        or 0
    )
    counts = CountsBlock(
        tickets_total=tickets_total,
        tickets_active=tickets_active,
        hub_issues_total=hub_issues_total,
        customers_total=customers_total,
        users_total=users_total,
        notifications_pending=notifications_pending,
    )

    # ---- routing -----------------------------------------------------
    auto_assigned = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.deleted_at.is_(None),
                Ticket.assigned_user_id.is_not(None),
            )
        )
        or 0
    )
    auto_hit_rate = auto_assigned / tickets_total if tickets_total else 0.0
    routing = RoutingBlock(
        tickets_total=tickets_total,
        auto_assigned=auto_assigned,
        auto_hit_rate=round(auto_hit_rate, 4),
    )

    # ---- supervisor relink -------------------------------------------
    linked_tickets = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.deleted_at.is_(None),
                Ticket.hub_issue_id.is_not(None),
            )
        )
        or 0
    )
    relink_count = (
        db.scalar(
            select(func.count(TicketHubIssueHistory.id)).where(
                TicketHubIssueHistory.effective_to.is_not(None)
            )
        )
        or 0
    )
    relink_rate = relink_count / linked_tickets if linked_tickets else 0.0
    supervisor = SupervisorBlock(
        linked_tickets=linked_tickets,
        relink_count=relink_count,
        relink_rate=round(relink_rate, 4),
    )

    # ---- customer dedup ----------------------------------------------
    identities_total = (
        db.scalar(
            select(func.count(CustomerIdentity.id)).where(CustomerIdentity.deleted_at.is_(None))
        )
        or 0
    )
    identities_matched = (
        db.scalar(
            select(func.count(CustomerIdentity.id)).where(
                CustomerIdentity.deleted_at.is_(None),
                CustomerIdentity.resolved_by_key != "none",
            )
        )
        or 0
    )
    match_rate = identities_matched / identities_total if identities_total else 0.0
    customer_dedup = CustomerDedupBlock(
        identities_total=identities_total,
        identities_matched=identities_matched,
        match_rate=round(match_rate, 4),
    )

    # ---- SLA ---------------------------------------------------------
    notifications_total = db.scalar(select(func.count(NotificationLog.id))) or 0
    acknowledged = (
        db.scalar(
            select(func.count(NotificationLog.id)).where(
                NotificationLog.acknowledged_at.is_not(None)
            )
        )
        or 0
    )
    escalated = (
        db.scalar(
            select(func.count(NotificationLog.id)).where(NotificationLog.escalated_at.is_not(None))
        )
        or 0
    )
    pending = notifications_pending  # already computed
    # Acknowledgement rate counts resolved (acked) vs. anything that exited
    # the pending state (acked + escalated). Pure pending notifications are
    # excluded from the denominator (they haven't had a chance yet).
    closed = acknowledged + escalated
    ack_rate = acknowledged / closed if closed else 0.0
    sla = SLABlock(
        notifications_total=notifications_total,
        pending=pending,
        acknowledged=acknowledged,
        escalated=escalated,
        acknowledgement_rate=round(ack_rate, 4),
    )

    # ---- webhook intake (24h) ---------------------------------------
    from datetime import UTC, datetime, timedelta
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    by_source_rows = db.execute(
        select(Ticket.source_code, func.count(Ticket.id))
        .where(Ticket.deleted_at.is_(None), Ticket.created_at >= cutoff)
        .group_by(Ticket.source_code)
    ).all()
    by_source = {row[0]: int(row[1]) for row in by_source_rows}
    intake_total = sum(by_source.values())
    # Approximate dedup rate: tickets sharing source_ticket_id with another row
    # would have been deduped at ingest time, but we don't keep a counter — surface 0
    # for now; D3 will record dedup attempts in agent_runs.
    webhook_intake = WebhookIntakeBlock(
        window_hours=24,
        by_source=by_source,
        total=intake_total,
        deduped_total=0,
    )

    return DashboardMetrics(
        counts=counts,
        routing=routing,
        supervisor=supervisor,
        customer_dedup=customer_dedup,
        sla=sla,
        webhook_intake=webhook_intake,
    )
