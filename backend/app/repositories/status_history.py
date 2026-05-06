"""status_history write helper — application-layer first (per spec §4.9)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import StatusHistory


class StatusHistoryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

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
