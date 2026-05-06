"""user_supervisors lookup (used by EscalationWorker)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import UserSupervisor


class UserSupervisorRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_supervisor_id(self, user_id: int) -> int | None:
        row = self._db.get(UserSupervisor, user_id)
        return row.supervisor_id if row else None

    def get_deputy_supervisor_id(self, user_id: int) -> int | None:
        row = self._db.get(UserSupervisor, user_id)
        return row.deputy_supervisor_id if row else None
