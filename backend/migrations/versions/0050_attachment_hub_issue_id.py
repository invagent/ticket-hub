"""add hub_issue_id to attachments table

Revision ID: 0050_attachment_hub_issue_id
Revises: 0049_backfill_hub_ticket_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_attachment_hub_issue_id"
down_revision: str | Sequence[str] | None = "0049_backfill_hub_ticket_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column(
            "hub_issue_id",
            sa.Integer(),
            sa.ForeignKey("hub_issues.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_attachments_hub_issue_id", "attachments", ["hub_issue_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_hub_issue_id", table_name="attachments")
    op.drop_column("attachments", "hub_issue_id")
