"""notification_log read/write helpers (SLA watcher + escalation worker)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NotificationLog


class NotificationLogRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(
        self,
        *,
        recipient_user_id: int,
        channel: str,
        notify_type: str,
        payload: dict[str, Any],
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> NotificationLog:
        row = NotificationLog(
            recipient_user_id=recipient_user_id,
            channel=channel,
            notify_type=notify_type,
            payload=payload,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def find_pending_escalation(
        self, *, older_than: timedelta, now: datetime | None = None
    ) -> list[NotificationLog]:
        """Notifications that are unacknowledged + un-escalated + older than threshold."""
        cutoff = (now or datetime.now(UTC)) - older_than
        stmt = (
            select(NotificationLog)
            .where(
                NotificationLog.acknowledged_at.is_(None),
                NotificationLog.escalated_at.is_(None),
                NotificationLog.sent_at < cutoff,
            )
            .order_by(NotificationLog.sent_at)
        )
        return list(self._db.execute(stmt).scalars().all())

    def acknowledge(self, notification_id: int, *, at: datetime | None = None) -> None:
        row = self._db.get(NotificationLog, notification_id)
        if row is None:
            return
        row.acknowledged_at = at or datetime.now(UTC)
        self._db.flush()

    def mark_escalated(
        self,
        notification_id: int,
        *,
        escalated_to_user_id: int,
        at: datetime | None = None,
    ) -> None:
        row = self._db.get(NotificationLog, notification_id)
        if row is None:
            return
        row.escalated_at = at or datetime.now(UTC)
        row.escalated_to_user_id = escalated_to_user_id
        self._db.flush()
