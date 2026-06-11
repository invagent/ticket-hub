"""Feishu user sync service — D2-E.

Bulk-pulls users from the Feishu contact API and upserts them into the local
`users` table. Used by `POST /api/admin/users/sync-from-feishu`.

Behavior:
  * Lookup is by `feishu_uid` (open_id). Existing rows have name/email/mobile/
    employee_no fields refreshed; their `role` is **never** changed by sync —
    admin must use PATCH /api/admin/users/{id} for that.
  * New rows are created with `role='member'`.
  * Soft-deleted users (deleted_at NOT NULL) get revived if Feishu still
    has them as activated.
  * Inactive Feishu users (`status.is_activated == false`) are
    skipped on first sync but **kept** if already present locally
    (admin chooses to deactivate or not).

Failure model: per-user errors are captured in `SyncReport.errors[]` so a
partial-failure batch still returns useful results to the admin UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from adapters.feishu import ContactUser, FeishuClient
from app.core.logging import get_logger
from app.repositories.user import UpsertResult, UserRepository

logger = get_logger(__name__)


@dataclass(slots=True)
class SyncReport:
    new_count: int = 0
    updated_count: int = 0
    revived_count: int = 0           # was soft-deleted, came back
    skipped_inactive: int = 0        # Feishu says deactivated and we don't have a local row
    errors: list[dict] = field(default_factory=list)
    new_user_ids: list[int] = field(default_factory=list)
    touched_user_ids: list[int] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return (
            self.new_count
            + self.updated_count
            + self.skipped_inactive
            + len(self.errors)
        )


class FeishuUserSyncService:
    """Coordinates Feishu adapter + user repository for bulk sync."""

    def __init__(self, db: Session, *, client: FeishuClient) -> None:
        self._db = db
        self._client = client
        self._repo = UserRepository(db)

    def sync_from_department(self, department_id: str) -> SyncReport:
        """Pull all users in a Feishu department and upsert them.

        `department_id="0"` syncs the entire tenant root (use with care).
        """
        users = self._client.list_users_by_department(department_id)
        logger.info(
            "feishu_sync_dept_fetched",
            department_id=department_id,
            count=len(users),
        )
        return self._apply(users)

    def sync_from_open_ids(self, open_ids: list[str]) -> SyncReport:
        """Targeted sync: only the specified open_ids."""
        report = SyncReport()
        for oid in open_ids:
            try:
                user = self._client.get_user_by_open_id(oid)
            except Exception as e:  # noqa: BLE001 — bubble up per-user
                report.errors.append({"open_id": oid, "error": str(e)})
                logger.warning("feishu_sync_user_fetch_failed", open_id=oid, error=str(e))
                continue
            if user is None:
                report.errors.append({"open_id": oid, "error": "user not found in Feishu"})
                continue
            self._apply_one(user, report)
        return report

    def _apply(self, users: list[ContactUser]) -> SyncReport:
        report = SyncReport()
        for user in users:
            self._apply_one(user, report)
        return report

    def _apply_one(self, user: ContactUser, report: SyncReport) -> None:
        if not user.is_activated:
            existing = self._repo.get_by_feishu_uid(user.open_id, include_deleted=True)
            if existing is None:
                report.skipped_inactive += 1
                return
            # Already in local DB — leave it alone; admin can soft-delete via PATCH

        try:
            # Fallback for name in this priority:
            #   飞书 name (best) → employee_no → email local-part → open_id suffix
            # (Feishu's contact API may return name=None when the app's
            # 通讯录数据范围 is not configured to include this user.)
            display_name = (
                user.name
                or user.employee_no
                or (user.email.split("@", 1)[0] if user.email else "")
                or f"feishu-{user.open_id[-8:]}"
            )
            result: UpsertResult = self._repo.upsert_by_feishu_uid(
                feishu_uid=user.open_id,
                name=display_name,
                email=user.email or None,
                mobile=user.mobile or None,
                employee_no=user.employee_no or None,
            )
        except Exception as e:  # noqa: BLE001
            report.errors.append({"open_id": user.open_id, "error": str(e)})
            logger.exception("feishu_sync_upsert_failed", open_id=user.open_id)
            return

        if result.created:
            report.new_count += 1
            report.new_user_ids.append(result.user.id)
        else:
            report.updated_count += 1
            if "deleted_at" in result.fields_updated:
                report.revived_count += 1
        report.touched_user_ids.append(result.user.id)
