"""D4 优化 v2: skill_prompts + skill_prompt_history — DB 化版本提示词.

Revision ID: 0013_v2_skill_prompts
Revises: 0012_d4_attachments
Create Date: 2026-06-26

提示词 DB 化 + 版本化 + 页面可编辑（对标 sample t_skill_md）。
prompt_store 读表覆盖 prompts/*.md；DB 无行回落文件。seed 由 import 脚本/端点完成。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_v2_skill_prompts"
down_revision: str | Sequence[str] | None = "0012_d4_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_prompts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.String(8), nullable=False, server_default="llm"),
        sa.Column("editable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("frontmatter", sa.JSON, nullable=True),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("type IN ('llm','code')", name="ck_skill_prompts_type"),
    )
    op.create_index("ix_skill_prompts_name", "skill_prompts", ["name"], unique=True)

    op.create_table(
        "skill_prompt_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("name", "version", name="uq_skill_prompt_history_version"),
    )
    op.create_index(
        "ix_skill_prompt_history_name", "skill_prompt_history", ["name", "version"]
    )


def downgrade() -> None:
    op.drop_index("ix_skill_prompt_history_name", table_name="skill_prompt_history")
    op.drop_table("skill_prompt_history")
    op.drop_index("ix_skill_prompts_name", table_name="skill_prompts")
    op.drop_table("skill_prompts")
