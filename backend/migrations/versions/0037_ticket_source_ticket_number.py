"""tickets 加 source_ticket_number（来源工单编号，展示/搜索用）.

Revision ID: 0037_ticket_source_ticket_number
Revises: 0036_outbox_return_kind

KSM 工单的 source_ticket_id 存的是 billId（长串 id），人看的编号在 billNumber。
加列落库编号，供列表展示 + 搜索匹配；id 继续留 source_ticket_id 供后台流转。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_ticket_source_ticket_number"
down_revision: str | Sequence[str] | None = "0036_outbox_return_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("source_ticket_number", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "source_ticket_number")
