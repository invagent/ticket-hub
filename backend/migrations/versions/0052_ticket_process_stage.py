"""add process_stage column to tickets and backfill

Revision ID: 0052_ticket_process_stage
Revises: 0051_knowledge_base_items
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_ticket_process_stage"
down_revision: str | Sequence[str] | None = "0051_knowledge_base_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 在 tickets 表增加 process_stage 列，默认“服务处理”
    op.add_column(
        "tickets",
        sa.Column(
            "process_stage",
            sa.String(32),
            nullable=False,
            server_default="服务处理",
        ),
    )
    op.create_index("ix_tickets_process_stage", "tickets", ["process_stage"])

    # 2. 存量历史数据回填：
    # 2.1 已关单 / 已解决 / 已答复 / 转单退回的工单 → 环节设为“完成”
    op.execute(
        """
        UPDATE tickets
        SET process_stage = '完成'
        WHERE status IN ('closed', 'done', 'resolved', 'answered', 'transferred_return')
        """
    )

    # 2.2 挂载了研发任务且已推送到 Linear（有 linear_uuid），当前处于研发在途状态（processing/in_progress/created）且非终态的工单 → 环节设为“研发处理”
    op.execute(
        """
        UPDATE tickets
        SET process_stage = '研发处理'
        FROM hub_issues
        WHERE tickets.hub_issue_id = hub_issues.id
          AND hub_issues.linear_uuid IS NOT NULL
          AND hub_issues.status IN ('processing', 'in_progress', 'created')
          AND tickets.status NOT IN ('closed', 'done', 'resolved', 'answered', 'transferred_return')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_process_stage", table_name="tickets")
    op.drop_column("tickets", "process_stage")
