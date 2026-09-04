"""hub_issues 加 root_cause_analysis（分析根因，推研发 handleDescription 用）.

Revision ID: 0042_hub_root_cause_analysis
Revises: 0041_dispatch_log_ticket_id

工单详情页「分析根因」输入框此前一直未接后端字段，纯前端摆设。补一列纯文本
字段承载，供推研发（webhook_push.build_webhook_fields）的 handleDescription
读取，不再复用 canonical_body（避免与 description 字段重复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_hub_root_cause_analysis"
down_revision: str | Sequence[str] | None = "0041_dispatch_log_ticket_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hub_issues", sa.Column("root_cause_analysis", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("hub_issues", "root_cause_analysis")
