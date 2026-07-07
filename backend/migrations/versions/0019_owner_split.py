"""ADR-0016 P4: owner-split 子 issue 跟踪表 + sync_outbox kind 扩 progress_note.

Revision ID: 0019_owner_split
Revises: 0018_skill_three_slots
Create Date: 2026-07-07

hub_issue_linear_issues：1 hub_issue 挂 N 个 Linear 子 issue（主管手动按责任人
分解）。每子 issue Done → 自动进度通知「含 n 子任务，本次第 x 个，剩 n-x」：
x<n 走新 kind='progress_note'（KSM handleKsmOrder is_deal=False 只回复不关单），
仅 x=n 最后一条走 release_note 关单。ck_sync_outbox_kind 扩枚举。
（SQLite 测试 schema 来自 models metadata，不走迁移。）
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_owner_split"
down_revision: str | Sequence[str] | None = "0018_skill_three_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hub_issue_linear_issues",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "hub_issue_id", sa.Integer(), sa.ForeignKey("hub_issues.id"), nullable=False
        ),
        sa.Column("linear_uuid", sa.String(64), nullable=False, unique=True),
        sa.Column("linear_identifier", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column(
            "assignee_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        # 镜像 Linear 列名 + state_type（同 hub_issues.linear_status 剧本）
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("state_type", sa.String(16), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        # 该子 issue 的进度通知已入队（轮询幂等防重）
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_hub_issue_linear_issues_hub", "hub_issue_linear_issues", ["hub_issue_id"]
    )

    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status','supply','release_note','progress_note')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status','supply','release_note')",
    )
    op.drop_index("ix_hub_issue_linear_issues_hub", table_name="hub_issue_linear_issues")
    op.drop_table("hub_issue_linear_issues")
