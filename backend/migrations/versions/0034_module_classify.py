"""ai module classify: tickets predicted_product_line_code/module + confidence + ts

Revision ID: 0034_module_classify
Revises: 0033_ksm_takeover
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034_module_classify"
down_revision: str | None = "0033_ksm_takeover"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("predicted_product_line_code", sa.String(64), nullable=True),
    )
    op.add_column("tickets", sa.Column("predicted_module", sa.String(128), nullable=True))
    op.add_column(
        "tickets",
        sa.Column("predicted_module_confidence", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("module_classified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "module_classified_at")
    op.drop_column("tickets", "predicted_module_confidence")
    op.drop_column("tickets", "predicted_module")
    op.drop_column("tickets", "predicted_product_line_code")
