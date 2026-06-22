"""D4 第③段: attachments — Vision 多模态 + ai_cs source 种子.

Revision ID: 0012_d4_attachments
Revises: 0011_d4_sync_outbox
Create Date: 2026-06-12

attachments: ticket 附件（截图为主）+ Vision 抽取结果。
sources 新增 'ai_cs'：AI 客服 escalation 工单的来源（cs-escalation webhook）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_d4_attachments"
down_revision: str | Sequence[str] | None = "0011_d4_sync_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("filename", sa.String(512), nullable=True),
        sa.Column("mime", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="image"),
        sa.Column("vision_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("vision_model", sa.String(64), nullable=True),
        sa.Column("vision_cost_usd", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('image','pdf','video','other')", name="ck_attachments_kind"),
        sa.CheckConstraint(
            "vision_status IN ('pending','extracted','skipped','failed')",
            name="ck_attachments_vision_status",
        ),
    )
    op.create_index("ix_attachments_ticket", "attachments", ["ticket_id"])
    op.create_index("ix_attachments_vision_pending", "attachments", ["vision_status"])

    op.execute(
        sa.text("""
            INSERT INTO sources (code, name, is_active)
            VALUES ('ai_cs', 'AI 客服', true)
            ON CONFLICT (code) DO NOTHING
            """)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sources WHERE code = 'ai_cs'"))
    op.drop_index("ix_attachments_vision_pending", table_name="attachments")
    op.drop_index("ix_attachments_ticket", table_name="attachments")
    op.drop_table("attachments")
