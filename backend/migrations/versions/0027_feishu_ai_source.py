"""feishu_ai source 种子

Revision ID: 0027_feishu_ai_source
Revises: 0026_op_status_drop_resupplied

sources 新增 'feishu_ai'：飞书AI 工单来源（/webhook/feishu_ai）。复用 ai_cs
载荷契约但走标准 triage 链。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0027_feishu_ai_source"
down_revision: str | None = "0026_op_status_drop_resupplied"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        sa.text("""
            INSERT INTO sources (code, name, is_active)
            VALUES ('feishu_ai', '飞书AI', true)
            ON CONFLICT (code) DO NOTHING
            """)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sources WHERE code = 'feishu_ai'"))
