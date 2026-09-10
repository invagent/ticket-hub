"""create knowledge_base_items table

Revision ID: 0051_knowledge_base_items
Revises: 0050_attachment_hub_issue_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_knowledge_base_items"
down_revision: str | Sequence[str] | None = "0050_attachment_hub_issue_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base_items",
        sa.Column("id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("product_line_code", sa.String(64), nullable=False),
        sa.Column("product_line_name", sa.String(128), nullable=False),
        sa.Column("module_code", sa.String(64), nullable=False),
        sa.Column("module_name", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachments", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_review','active','rejected','offline')",
            name="ck_knowledge_base_items_status",
        ),
    )
    op.create_index("ix_kb_items_status", "knowledge_base_items", ["status"])
    op.create_index(
        "ix_kb_items_product_module",
        "knowledge_base_items",
        ["product_line_code", "module_code"],
    )
    op.create_index("ix_kb_items_created_at", "knowledge_base_items", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_kb_items_created_at", table_name="knowledge_base_items")
    op.drop_index("ix_kb_items_product_module", table_name="knowledge_base_items")
    op.drop_index("ix_kb_items_status", table_name="knowledge_base_items")
    op.drop_table("knowledge_base_items")
