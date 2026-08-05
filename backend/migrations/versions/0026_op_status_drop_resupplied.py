"""drop resupplied from op_status check constraint

Revision ID: 0026_op_status_drop_resupplied
Revises: 0025_outbox_hub_nullable
"""

from __future__ import annotations

from alembic import op

revision: str = "0026_op_status_drop_resupplied"
down_revision: str | None = "0025_outbox_hub_nullable"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','exception')"
)
_OLD = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','resupplied','exception')"
)


def upgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _OLD)
