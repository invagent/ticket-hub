"""ksm auto takeover: tickets.ksm_takeover_status + ksm_takeover_error

Revision ID: 0033_ksm_takeover
Revises: 0032_dispatch_enhancements
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0033_ksm_takeover"
down_revision: str | None = "0032_dispatch_enhancements"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("ksm_takeover_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("ksm_takeover_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "ksm_takeover_error")
    op.drop_column("tickets", "ksm_takeover_status")
