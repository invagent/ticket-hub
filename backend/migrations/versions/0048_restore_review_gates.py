"""restore in-flight review gates (pending_review, pending_linear_review, pending)

Revision ID: 0048_restore_review_gates
Revises: 0047_unify_statuses
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048_restore_review_gates"
down_revision: str | Sequence[str] | None = "0047_unify_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 恢复 0047 迁移中被误归入 draft 的在途待审核/待确认网关任务（根据 status_history 最新记录对齐）
    op.execute(
        """
        UPDATE hub_issues h
        SET status = sh.to_status
        FROM status_history sh
        JOIN (
            SELECT entity_id, MAX(id) AS max_id
            FROM status_history
            WHERE entity_type = 'hub_issue'
            GROUP BY entity_id
        ) latest ON sh.id = latest.max_id
        WHERE h.id = sh.entity_id
          AND h.status = 'draft'
          AND sh.to_status IN ('pending_review', 'pending_linear_review', 'pending')
        """
    )


def downgrade() -> None:
    pass
