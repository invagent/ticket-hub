"""SLAWatcher — scan overdue tickets / hub_issues and write notification_log.

Per upgrade_plan.md §11.1 + §12.1 (SLA 健康度 ≥ 90% target):

  Default SLA thresholds (D2-C: configurable per product_line via the
  product_lines.sla_reply_hours / sla_resolve_hours columns; NULL on either
  column = fall back to the builtin defaults below):
    - ticket "first response": 4h since received_at, customer_replied_at IS NULL
    - hub_issue Operation:     4h since first_seen_at, status open
    - hub_issue Bug_fix:       8h since first_seen_at
    - hub_issue Demand:       24h since first_seen_at
    - hub_issue Internal_task: 8h since first_seen_at

Per-line override semantics:
  - sla_reply_hours   → used as the ticket "first response" threshold for
                        tickets carrying this product_line_code.
  - sla_resolve_hours → used as the hub_issue threshold for ALL types of
                        that product_line, replacing the per-type defaults
                        above. (Per-type-per-line is too granular for D2;
                        revisit in D6 if needed.)

Side effect: writes one notification_log row per overdue entity, addressed to
the assigned_user_id. If the entity has no assignee, falls through to
`fallback_recipient_id` (e.g., the on-call supervisor).

This is the SLA *detection* phase. The 2h-then-deputy escalation chain is in
`escalation.py`. SLAWatcher writes notifications; EscalationWorker re-targets
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import ProductLine
from app.repositories.notification_log import NotificationLogRepository
from app.repositories.ticket import HubIssueRepository, TicketRepository

logger = get_logger(__name__)


# Default thresholds — used when product_lines doesn't override.
DEFAULT_TICKET_REPLY_THRESHOLD = timedelta(hours=4)
DEFAULT_HUB_ISSUE_THRESHOLDS = {
    "Operation": timedelta(hours=4),
    "Bug_fix": timedelta(hours=8),
    "Demand": timedelta(hours=24),
    "Internal_task": timedelta(hours=8),
}


@dataclass(slots=True, frozen=True)
class SLAScanResult:
    """Summary of one scan() invocation."""

    notifications_written: int = 0
    overdue_ticket_ids: list[int] = field(default_factory=list)
    overdue_hub_issue_ids: list[int] = field(default_factory=list)
    skipped_unassigned: int = 0  # tickets with no assignee AND no fallback_recipient


class SLAWatcher:
    def __init__(
        self,
        db: Session,
        *,
        ticket_threshold: timedelta = DEFAULT_TICKET_REPLY_THRESHOLD,
        hub_issue_thresholds: dict[str, timedelta] | None = None,
        fallback_recipient_id: int | None = None,
    ) -> None:
        self._db = db
        self._ticket_repo = TicketRepository(db)
        self._hub_repo = HubIssueRepository(db)
        self._notif_repo = NotificationLogRepository(db)
        self._ticket_threshold = ticket_threshold
        self._hub_thresholds = hub_issue_thresholds or DEFAULT_HUB_ISSUE_THRESHOLDS
        self._fallback_recipient_id = fallback_recipient_id

    def _load_product_overrides(
        self,
    ) -> tuple[dict[str, timedelta], dict[str, timedelta]]:
        """Read product_lines table once per scan() — returns
        (reply_overrides, resolve_overrides) keyed by product_line_code,
        only including lines with non-NULL columns."""
        reply: dict[str, timedelta] = {}
        resolve: dict[str, timedelta] = {}
        rows = self._db.execute(select(ProductLine)).scalars().all()
        for r in rows:
            if r.sla_reply_hours is not None and r.sla_reply_hours > 0:
                reply[r.code] = timedelta(hours=r.sla_reply_hours)
            if r.sla_resolve_hours is not None and r.sla_resolve_hours > 0:
                resolve[r.code] = timedelta(hours=r.sla_resolve_hours)
        return reply, resolve

    def _ticket_threshold_for(
        self, product_line_code: str | None, reply_overrides: dict[str, timedelta]
    ) -> timedelta:
        """Return the effective reply threshold: per-line override if set,
        else the watcher-level default."""
        if product_line_code and product_line_code in reply_overrides:
            return reply_overrides[product_line_code]
        return self._ticket_threshold

    def _hub_threshold_for(
        self,
        type_: str,
        product_line_code: str | None,
        resolve_overrides: dict[str, timedelta],
    ) -> timedelta | None:
        """Per-line override (`sla_resolve_hours`) replaces per-type default
        when set. Otherwise return the type's default threshold."""
        if product_line_code and product_line_code in resolve_overrides:
            return resolve_overrides[product_line_code]
        return self._hub_thresholds.get(type_)

    def scan(self, *, now: datetime | None = None) -> SLAScanResult:
        ts_now = now or datetime.now(UTC)
        written = 0
        overdue_t: list[int] = []
        overdue_h: list[int] = []
        skipped = 0

        reply_overrides, resolve_overrides = self._load_product_overrides()

        # Pre-fetch with the *minimum* applicable threshold so we don't miss
        # tickets whose product_line has a smaller window. We then re-check
        # in Python with the row's specific threshold.
        min_reply = min(
            (self._ticket_threshold, *reply_overrides.values()),
            default=self._ticket_threshold,
        )
        min_resolve_per_type = {
            t: min(
                (default, *resolve_overrides.values()),
                default=default,
            )
            for t, default in self._hub_thresholds.items()
        }

        # tickets: customer not yet replied past threshold
        for t in self._ticket_repo.find_unreplied_overdue(
            threshold=min_reply, now=ts_now
        ):
            effective = self._ticket_threshold_for(t.product_line_code, reply_overrides)
            # Row may have been pulled because of a smaller min; verify against
            # this ticket's actual threshold.
            ra = t.received_at
            if ra is None:
                continue
            ra_aware = ra if ra.tzinfo else ra.replace(tzinfo=UTC)
            if (ts_now - ra_aware) < effective:
                continue
            recipient = t.assigned_user_id or self._fallback_recipient_id
            if recipient is None:
                skipped += 1
                logger.warning("sla_skip_unassigned", entity_type="ticket", entity_id=t.id)
                continue
            self._notif_repo.add(
                recipient_user_id=recipient,
                channel="feishu_bot",
                notify_type="sla_overdue",
                related_entity_type="ticket",
                related_entity_id=t.id,
                payload={
                    "ticket_id": t.id,
                    "short_code": t.short_code,
                    "title": t.title,
                    "received_at": t.received_at.isoformat() if t.received_at else None,
                    "threshold_hours": effective.total_seconds() / 3600,
                    "product_line_code": t.product_line_code,
                    "reason": "no_customer_reply",
                },
            )
            overdue_t.append(t.id)
            written += 1

        # hub_issues: per-type defaults, overridable per product_line
        for h in self._hub_repo.find_overdue_by_type(
            type_thresholds=min_resolve_per_type, now=ts_now
        ):
            effective = self._hub_threshold_for(h.type, h.product_line_code, resolve_overrides)
            if effective is None:
                continue
            fs = h.first_seen_at
            if fs is None:
                continue
            fs_aware = fs if fs.tzinfo else fs.replace(tzinfo=UTC)
            if (ts_now - fs_aware) < effective:
                continue
            recipient = h.assigned_user_id or self._fallback_recipient_id
            if recipient is None:
                skipped += 1
                logger.warning("sla_skip_unassigned", entity_type="hub_issue", entity_id=h.id)
                continue
            self._notif_repo.add(
                recipient_user_id=recipient,
                channel="feishu_bot",
                notify_type="sla_overdue",
                related_entity_type="hub_issue",
                related_entity_id=h.id,
                payload={
                    "hub_issue_id": h.id,
                    "short_code": h.short_code,
                    "type": h.type,
                    "title": h.title,
                    "first_seen_at": h.first_seen_at.isoformat() if h.first_seen_at else None,
                    "threshold_hours": effective.total_seconds() / 3600,
                    "product_line_code": h.product_line_code,
                },
            )
            overdue_h.append(h.id)
            written += 1

        return SLAScanResult(
            notifications_written=written,
            overdue_ticket_ids=overdue_t,
            overdue_hub_issue_ids=overdue_h,
            skipped_unassigned=skipped,
        )
