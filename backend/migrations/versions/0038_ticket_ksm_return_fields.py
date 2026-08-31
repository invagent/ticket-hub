"""tickets 加 KSM 退回持久化字段（受理节点 opercacheId + 当前节点）.

Revision ID: 0038_ticket_ksm_return_fields
Revises: 0037_ticket_source_ticket_number

退回 KSM 需要「受理节点 opercacheId」（退回目标）+「当前节点 node.id」（退回源），
但 notice 24h 过期后拿不到实时详情，source_payload 又是入库快照。takeover 成功
后把这两个值持久化到 ticket，退回 sender 直接用，不再依赖 notice。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_ticket_ksm_return_fields"
down_revision: str | Sequence[str] | None = "0037_ticket_source_ticket_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("ksm_accept_opercache_id", sa.String(64), nullable=True))
    op.add_column("tickets", sa.Column("ksm_current_node_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "ksm_current_node_id")
    op.drop_column("tickets", "ksm_accept_opercache_id")
