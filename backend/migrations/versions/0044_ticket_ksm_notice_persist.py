"""tickets 加 KSM notice 凭证持久化列（不设过期时间）.

Revision ID: 0044_ticket_ksm_notice_persist
Revises: 0043_ksm_source_fields

Redis NoticeStore 的 24h TTL 是本系统自设的保守策略，非 KSM 服务端真实有效期
（2026-09 实测：4 天前的旧 notice 依然能成功拉取详情）。每次收到 webhook 推送
同步落库这两列，Redis 过期后可回落，不再直接拒绝退回/重拉详情。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_ticket_ksm_notice_persist"
down_revision: str | Sequence[str] | None = "0043_ksm_source_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("ksm_notice_num", sa.String(64), nullable=True))
    op.add_column("tickets", sa.Column("ksm_subscribe_num", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "ksm_subscribe_num")
    op.drop_column("tickets", "ksm_notice_num")
