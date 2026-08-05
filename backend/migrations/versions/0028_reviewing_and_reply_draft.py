"""add reviewing op_status + reply_is_draft

Revision ID: 0028_reviewing_and_reply_draft
Revises: 0027_feishu_ai_source
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0028_reviewing_and_reply_draft"
down_revision: str | None = "0027_feishu_ai_source"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','reviewing','exception')"
)
_OLD = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','exception')"
)


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("reply_is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _OLD)
    op.drop_column("hub_issues", "reply_is_draft")
