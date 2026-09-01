"""tickets 加处理人标记「AI 自动答复有问题」送反思诊断的时间戳.

Revision ID: 0039_ticket_diagnosis_flag
Revises: 0038_ticket_ksm_return_fields

KSM/智齿运营单经系统内置 AI 自动答复（同一套外部 AI 客服 skill/知识库）后，处理人
本人若发现答复有问题，可标记送反思诊断。真实 ai_cs escalation 工单该列恒为 NULL；
加索引供 escalation-pending-diagnosis / reflect-tickets 两处列表查询过滤用。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_ticket_diagnosis_flag"
down_revision: str | Sequence[str] | None = "0038_ticket_ksm_return_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tickets", sa.Column("diagnosis_flagged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_tickets_diagnosis_flagged", "tickets", ["diagnosis_flagged_at"])


def downgrade() -> None:
    op.drop_index("ix_tickets_diagnosis_flagged", table_name="tickets")
    op.drop_column("tickets", "diagnosis_flagged_at")
