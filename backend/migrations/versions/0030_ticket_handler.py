"""ticket handler_user_id 处理人（区别于路由分工责任人 assigned_user_id）

Revision ID: 0030_ticket_handler
Revises: 0029_dispatch_engine
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0030_ticket_handler"
down_revision: str | None = "0029_dispatch_engine"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("handler_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_tickets_handler_user_id", "tickets", ["handler_user_id"])
    # 回填：存量处理人 = 责任人（路由分工）
    op.execute(
        "UPDATE tickets SET handler_user_id = assigned_user_id WHERE handler_user_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_handler_user_id", table_name="tickets")
    op.drop_column("tickets", "handler_user_id")
