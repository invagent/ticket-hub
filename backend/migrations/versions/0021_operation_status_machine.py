"""operation status machine fields

Revision ID: 0021_operation_status_machine
Revises: 0020_knowledge_op_role
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0021_operation_status_machine"
down_revision: str | None = "0020_knowledge_op_role"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hub_issues", sa.Column("op_status", sa.String(length=16), nullable=True))
    op.add_column("hub_issues", sa.Column("op_handler", sa.String(length=64), nullable=True))
    op.add_column(
        "hub_issues",
        sa.Column("reject_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hub_issues",
        sa.Column("op_status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_hub_issues_op_status",
        "hub_issues",
        "op_status IS NULL OR op_status IN "
        "('processing','answered','closed','supplementing','resupplied','exception')",
    )
    # 回填现存 Operation hub：reply_v>=1 → answered，否则 processing；handler=agent
    op.execute(
        """
        UPDATE hub_issues
        SET op_status = CASE WHEN reply_content_version >= 1 THEN 'answered' ELSE 'processing' END,
            op_handler = 'agent',
            op_status_changed_at = COALESCE(status_changed_at, created_at)
        WHERE type = 'Operation' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.drop_column("hub_issues", "op_status_changed_at")
    op.drop_column("hub_issues", "reject_count")
    op.drop_column("hub_issues", "op_handler")
    op.drop_column("hub_issues", "op_status")
