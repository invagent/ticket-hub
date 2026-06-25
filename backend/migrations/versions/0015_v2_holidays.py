"""D4 优化 v2: holidays — SLA 工作日/节假日感知日历.

Revision ID: 0015_v2_holidays
Revises: 0014_v2_hub_embedding
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_v2_holidays"
down_revision: str | Sequence[str] | None = "0014_v2_hub_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holidays",
        sa.Column("holiday_date", sa.Date, primary_key=True, autoincrement=False),
        sa.Column("day_type", sa.String(8), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("day_type IN ('holiday','workday')", name="ck_holidays_day_type"),
    )


def downgrade() -> None:
    op.drop_table("holidays")
