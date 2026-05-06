"""SLAWatcher — scan overdue tickets / hub_issues and write notification_log.

Per upgrade_plan.md §11.1 + §12.1 (SLA 健康度 ≥ 90% target):

  Default SLA thresholds (configurable per product_line later):
    - ticket "first response": 4h since received_at, customer_replied_at IS NULL
    - hub_issue Operation:     4h since first_seen_at, status open
    - hub_issue Bug_fix:       8h since first_seen_at
    - hub_issue Demand:       24h since first_seen_at
    - hub_issue Internal_task: 8h since first_seen_at

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

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.repositories.notification_log import NotificationLogRepository
from app.repositories.ticket import HubIssueRepository, TicketRepository

logger = get_logger(__name__)


# Default thresholds — keep here for D1; D2 moves to config table per product_line.
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

    def scan(self, *, now: datetime | None = None) -> SLAScanResult:
        ts_now = now or datetime.now(UTC)
        written = 0
        overdue_t: list[int] = []
        overdue_h: list[int] = []
        skipped = 0

        # tickets: customer not yet replied past threshold
        for t in self._ticket_repo.find_unreplied_overdue(
            threshold=self._ticket_threshold, now=ts_now
        ):
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
                    "threshold_hours": self._ticket_threshold.total_seconds() / 3600,
                    "reason": "no_customer_reply",
                },
            )
            overdue_t.append(t.id)
            written += 1

        # hub_issues: per-type thresholds
        for h in self._hub_repo.find_overdue_by_type(
            type_thresholds=self._hub_thresholds, now=ts_now
        ):
            recipient = h.assigned_user_id or self._fallback_recipient_id
            if recipient is None:
                skipped += 1
                logger.warning("sla_skip_unassigned", entity_type="hub_issue", entity_id=h.id)
                continue
            threshold = self._hub_thresholds.get(h.type)
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
                    "threshold_hours": threshold.total_seconds() / 3600 if threshold else None,
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
