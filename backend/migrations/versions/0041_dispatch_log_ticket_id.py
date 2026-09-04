"""dispatch_log 加 ticket_id，hub_issue_id 改可空.

Revision ID: 0041_dispatch_log_ticket_id
Revises: 0040_hub_owner_rotation

派单提前到 ticket 入库阶段（此时 hub_issue 尚不存在），dispatch_log 的落地
主键随之从 hub_issue 换成 ticket：新增 ticket_id（NOT NULL），hub_issue_id
改为可空（历史行迁移时用其关联的最早一条 ticket 回填 ticket_id；新行不再
写 hub_issue_id，等真正毕业时如需要再由调用方另行补写）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_dispatch_log_ticket_id"
down_revision: str | Sequence[str] | None = "0040_hub_owner_rotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dispatch_log",
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=True),
    )
    op.create_index("ix_dispatch_log_ticket_id", "dispatch_log", ["ticket_id"])

    # 历史行回填：取该 hub_issue 下最早一条 ticket 作为 ticket_id（镜像
    # dispatch/engine.py _hub_source_code 曾用的反查口径）。
    op.execute(
        """
        UPDATE dispatch_log
        SET ticket_id = (
            SELECT t.id FROM tickets t
            WHERE t.hub_issue_id = dispatch_log.hub_issue_id
            ORDER BY t.id ASC
            LIMIT 1
        )
        WHERE ticket_id IS NULL
        """
    )
    # 极少数查不到关联 ticket 的历史行（hub 已被删除等）留 NULL，不阻塞迁移；
    # 不对 ticket_id 加 NOT NULL 约束到列级，靠应用层保证新行必填。
    op.alter_column("dispatch_log", "hub_issue_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("dispatch_log", "hub_issue_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_dispatch_log_ticket_id", table_name="dispatch_log")
    op.drop_column("dispatch_log", "ticket_id")
