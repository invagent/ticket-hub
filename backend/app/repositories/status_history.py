"""status_history write helper — application-layer first (per spec §4.9)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StatusHistory


class StatusHistoryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_for_entity(
        self, *, entity_type: str, entity_id: int, limit: int = 200
    ) -> list[StatusHistory]:
        """All status transitions for a (entity_type, entity_id), oldest first."""
        stmt = (
            select(StatusHistory)
            .where(
                StatusHistory.entity_type == entity_type,
                StatusHistory.entity_id == entity_id,
            )
            .order_by(StatusHistory.changed_at.asc(), StatusHistory.id.asc())
            .limit(min(max(limit, 1), 1000))
        )
        return list(self._db.execute(stmt).scalars().all())

    def record(
        self,
        *,
        entity_type: str,  # 'ticket' | 'hub_issue'
        entity_id: int,
        from_status: str | None,
        to_status: str,
        changed_by: str,  # 'agent:dedup' | 'user:zhangsan' | 'cascade:status_cascade' | 'system:auto'
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StatusHistory:
        row = StatusHistory(
            entity_type=entity_type,
            entity_id=entity_id,
            from_status=from_status,
            to_status=to_status,
            changed_by=changed_by,
            reason=reason,
            metadata_=metadata,
        )
        self._db.add(row)
        self._db.flush()
        return row
