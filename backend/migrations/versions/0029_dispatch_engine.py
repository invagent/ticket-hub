"""dispatch 引擎：4 表 + hub_issues.op_handler_user_id

Revision ID: 0029_dispatch_engine
Revises: 0028_reviewing_and_reply_draft
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0029_dispatch_engine"
down_revision: str | None = "0028_reviewing_and_reply_draft"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "dispatch_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("match_sources", sa.JSON(), nullable=False),
        sa.Column("match_product_lines", sa.JSON(), nullable=False),
        sa.Column("match_modules", sa.JSON(), nullable=False),
        sa.Column("match_sla", sa.JSON(), nullable=False),
        sa.Column("dispatch_mode", sa.String(16), nullable=False),
        sa.Column("rule_type", sa.String(16), nullable=False, server_default="primary"),
        sa.Column(
            "overflow_rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=True
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("dispatch_mode IN ('count','ratio')", name="ck_dispatch_rules_mode"),
        sa.CheckConstraint("rule_type IN ('primary','overflow')", name="ck_dispatch_rules_type"),
    )
    op.create_index(
        "ix_dispatch_rules_active_priority", "dispatch_rules", ["is_active", "priority"]
    )
    op.create_table(
        "dispatch_assignees",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("alloc_value", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("daily_cap", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(8), nullable=False, server_default="main"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("tier IN ('main','overflow')", name="ck_dispatch_assignees_tier"),
    )
    op.create_index("ix_dispatch_assignees_rule", "dispatch_assignees", ["rule_id"])
    op.create_table(
        "dispatch_config",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(128), nullable=False),
    )
    op.create_table(
        "dispatch_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hub_issue_id", sa.Integer(), sa.ForeignKey("hub_issues.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=True),
        sa.Column("assignee_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tier_hit", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_dispatch_log_hub", "dispatch_log", ["hub_issue_id"])
    op.create_index("ix_dispatch_log_rule_created", "dispatch_log", ["rule_id", "created_at"])
    op.add_column(
        "hub_issues",
        sa.Column("op_handler_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hub_issues_op_handler_user", "hub_issues", ["op_handler_user_id"])


def downgrade() -> None:
    op.drop_index("ix_hub_issues_op_handler_user", table_name="hub_issues")
    op.drop_column("hub_issues", "op_handler_user_id")
    op.drop_index("ix_dispatch_log_rule_created", table_name="dispatch_log")
    op.drop_index("ix_dispatch_log_hub", table_name="dispatch_log")
    op.drop_table("dispatch_log")
    op.drop_table("dispatch_config")
    op.drop_index("ix_dispatch_assignees_rule", table_name="dispatch_assignees")
    op.drop_table("dispatch_assignees")
    op.drop_index("ix_dispatch_rules_active_priority", table_name="dispatch_rules")
    op.drop_table("dispatch_rules")
