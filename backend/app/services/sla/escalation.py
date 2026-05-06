"""EscalationWorker — deputy / supervisor 兜底 (decision D6).

Per upgrade_plan.md §11.1 / §4.12:

  Every 10 minutes, scan notification_log for entries that are:
    - acknowledged_at IS NULL
    - escalated_at IS NULL
    - sent_at older than `escalation_after` (default 2h)

  For each: target = deputy_supervisor_id of recipient
            ?? else: supervisor_id of recipient

  If target found:
    1. Write a NEW notification_log row addressed to target
       (notify_type='escalation', payload includes original notification id)
    2. Mark original row's escalated_at = now() and escalated_to_user_id = target

  Two-step chain emerges naturally: when the escalation notification itself
  goes 2h unacknowledged, it gets picked up next round → escalates again.

  No-target case (no deputy AND no supervisor): we leave escalated_at NULL so
  ops can audit + manually intervene; the notification stays in the pending
  list until human acks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.repositories.notification_log import NotificationLogRepository
from app.repositories.user_supervisor import UserSupervisorRepository

logger = get_logger(__name__)


DEFAULT_ESCALATION_AFTER = timedelta(hours=2)


@dataclass(slots=True, frozen=True)
class EscalationStep:
    notification_id: int
    original_recipient_id: int
    escalated_to_user_id: int
    via: str  # "deputy" | "supervisor"


@dataclass(slots=True)
class EscalationRunResult:
    escalated: list[EscalationStep] = field(default_factory=list)
    no_target: list[int] = field(default_factory=list)  # notification ids that hit a dead end


class EscalationWorker:
    def __init__(
        self,
        db: Session,
        *,
        escalation_after: timedelta = DEFAULT_ESCALATION_AFTER,
    ) -> None:
        self._db = db
        self._notif_repo = NotificationLogRepository(db)
        self._sup_repo = UserSupervisorRepository(db)
        self._escalation_after = escalation_after

    def escalate_pending(self, *, now: datetime | None = None) -> EscalationRunResult:
        ts_now = now or datetime.now(UTC)
        result = EscalationRunResult()

        pending = self._notif_repo.find_pending_escalation(
            older_than=self._escalation_after, now=ts_now
        )
        for n in pending:
            target_id, via = self._find_target(n.recipient_user_id)
            if target_id is None:
                logger.warning(
                    "escalation_no_target",
                    notification_id=n.id,
                    recipient_user_id=n.recipient_user_id,
                )
                result.no_target.append(n.id)
                continue

            new_payload: dict[str, Any] = {
                "escalation_of_notification_id": n.id,
                "original_recipient_user_id": n.recipient_user_id,
                "via": via,
                **n.payload,
            }
            self._notif_repo.add(
                recipient_user_id=target_id,
                channel=n.channel,
                notify_type="escalation",
                payload=new_payload,
                related_entity_type=n.related_entity_type,
                related_entity_id=n.related_entity_id,
            )
            self._notif_repo.mark_escalated(n.id, escalated_to_user_id=target_id, at=ts_now)
            result.escalated.append(
                EscalationStep(
                    notification_id=n.id,
                    original_recipient_id=n.recipient_user_id,
                    escalated_to_user_id=target_id,
                    via=via,
                )
            )
        return result

    def _find_target(self, recipient_user_id: int) -> tuple[int | None, str]:
        deputy = self._sup_repo.get_deputy_supervisor_id(recipient_user_id)
        if deputy is not None and deputy != recipient_user_id:
            return deputy, "deputy"
        supervisor = self._sup_repo.get_supervisor_id(recipient_user_id)
        if supervisor is not None and supervisor != recipient_user_id:
            return supervisor, "supervisor"
        return None, ""
