"""user_supervisors lookup + mutate (used by EscalationWorker + admin API)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import UserSupervisor


class UserSupervisorRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- read --------------------------------------------------------

    def get(self, user_id: int) -> UserSupervisor | None:
        return self._db.get(UserSupervisor, user_id)

    def get_supervisor_id(self, user_id: int) -> int | None:
        row = self._db.get(UserSupervisor, user_id)
        return row.supervisor_id if row else None

    def get_deputy_supervisor_id(self, user_id: int) -> int | None:
        row = self._db.get(UserSupervisor, user_id)
        return row.deputy_supervisor_id if row else None

    # ---- write -------------------------------------------------------

    def upsert(
        self,
        *,
        user_id: int,
        supervisor_id: int,
        deputy_supervisor_id: int | None = None,
    ) -> UserSupervisor:
        """Set or replace the supervisor (and optional deputy) for `user_id`.

        Enforces no self-supervision (CHECK constraint at DB layer).
        Same-supervisor-as-deputy is allowed (admin's responsibility to avoid).
        """
        if supervisor_id == user_id:
            raise ValueError("supervisor_id cannot equal user_id")
        if deputy_supervisor_id == user_id:
            raise ValueError("deputy_supervisor_id cannot equal user_id")
        existing = self._db.get(UserSupervisor, user_id)
        if existing is None:
            row = UserSupervisor(
                user_id=user_id,
                supervisor_id=supervisor_id,
                deputy_supervisor_id=deputy_supervisor_id,
            )
            self._db.add(row)
            self._db.flush()
            return row
        existing.supervisor_id = supervisor_id
        existing.deputy_supervisor_id = deputy_supervisor_id
        self._db.flush()
        return existing

    def clear(self, user_id: int) -> bool:
        """Remove the supervisor relationship. Returns True if a row was deleted."""
        existing = self._db.get(UserSupervisor, user_id)
        if existing is None:
            return False
        self._db.delete(existing)
        self._db.flush()
        return True
