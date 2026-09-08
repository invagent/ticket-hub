"""add transferred_return op_status and expand column length to 32

Revision ID: 0045_op_transferred_return
Revises: 0044_ticket_ksm_notice_persist
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_op_transferred_return"
down_revision: str | Sequence[str] | None = "0044_ticket_ksm_notice_persist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','reviewing','exception','transferred_return')"
)
_OLD = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','reviewing','exception')"
)


def upgrade() -> None:
    op.alter_column(
        "hub_issues",
        "op_status",
        type_=sa.String(32),
        existing_type=sa.String(16),
        existing_nullable=True,
    )
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _OLD)
    op.alter_column(
        "hub_issues",
        "op_status",
        type_=sa.String(16),
        existing_type=sa.String(32),
        existing_nullable=True,
    )
