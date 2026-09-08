"""hub_issues add ticket_id column for subtask association

Revision ID: 0046_hub_issue_ticket_id
Revises: 0045_op_transferred_return
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_hub_issue_ticket_id"
down_revision: str | Sequence[str] | None = "0045_op_transferred_return"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=True),
    )
    op.create_index("ix_hub_issues_ticket_id", "hub_issues", ["ticket_id"])
    op.drop_constraint("ck_hub_issues_operation_fields", "hub_issues", type_="check")
    op.create_check_constraint(
        "ck_hub_issues_operation_fields",
        "hub_issues",
        "type='Operation' OR ticket_id IS NOT NULL OR (reply_content IS NULL AND reply_authored_by IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_operation_fields", "hub_issues", type_="check")
    op.create_check_constraint(
        "ck_hub_issues_operation_fields",
        "hub_issues",
        "type='Operation' OR (reply_content IS NULL AND reply_authored_by IS NULL)",
    )
    op.drop_index("ix_hub_issues_ticket_id", table_name="hub_issues")
    op.drop_column("hub_issues", "ticket_id")
