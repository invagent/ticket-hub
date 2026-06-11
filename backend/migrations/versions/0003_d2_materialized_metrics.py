"""D2: materialized_metrics table — Celery-refreshed dashboard snapshot.

Revision ID: 0003_d2_materialized_metrics
Revises: 0002_d1_business_tables
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_d2_materialized_metrics"
down_revision: str | Sequence[str] | None = "0002_d1_business_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "materialized_metrics",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slot_key", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("metrics_json", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_materialized_metrics_slot_key",
        "materialized_metrics",
        ["slot_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_materialized_metrics_slot_key", table_name="materialized_metrics")
    op.drop_table("materialized_metrics")
