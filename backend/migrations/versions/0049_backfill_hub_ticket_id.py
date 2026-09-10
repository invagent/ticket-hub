"""backfill hub_issues ticket_id for primary hub issues

Revision ID: 0049_backfill_hub_ticket_id
Revises: 0048_restore_review_gates
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0049_backfill_hub_ticket_id"
down_revision: str | Sequence[str] | None = "0048_restore_review_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 补齐所有主任务漏填的 ticket_id，使 ck_hub_issues_operation_fields 检查约束满足
    op.execute(
        """
        UPDATE hub_issues
        SET ticket_id = tickets.id
        FROM tickets
        WHERE tickets.hub_issue_id = hub_issues.id
          AND hub_issues.ticket_id IS NULL
        """
    )


def downgrade() -> None:
    pass
