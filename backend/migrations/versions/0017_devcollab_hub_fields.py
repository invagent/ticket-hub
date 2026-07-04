"""研发协同（2026-07 后台重构 批次5）: hub_issues 催办/发版/反馈/自查字段 + outbox kind.

Revision ID: 0017_devcollab_hub_fields
Revises: 0016_v2_outbox_supply_kind
Create Date: 2026-07-04

hub_issues 新增：
  urge_count / last_urged_at             催办计数（Linear comment）
  release_notified_at / release_note / fix_version / impact_versions  发版通知
  feedback_status / feedback_note / feedback_at   客户回访（pending/resolved/stillbad）
  self_found                             研发自查登记（无客户来源）
  status_changed_at                      当前状态进入时间（停留时长）

sync_outbox.kind 增加 'release_note'（发版通知出站，KSM sender 按 reply 剧本消费）。
SQLite 测试库走 models metadata，不经迁移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_devcollab_hub_fields"
down_revision: str | Sequence[str] | None = "0016_v2_outbox_supply_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("urge_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hub_issues", sa.Column("last_urged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "hub_issues", sa.Column("release_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("hub_issues", sa.Column("release_note", sa.Text(), nullable=True))
    op.add_column("hub_issues", sa.Column("fix_version", sa.String(64), nullable=True))
    op.add_column("hub_issues", sa.Column("impact_versions", sa.String(128), nullable=True))
    op.add_column("hub_issues", sa.Column("feedback_status", sa.String(16), nullable=True))
    op.add_column("hub_issues", sa.Column("feedback_note", sa.Text(), nullable=True))
    op.add_column(
        "hub_issues", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "hub_issues",
        sa.Column("self_found", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "hub_issues", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_hub_issues_feedback_status",
        "hub_issues",
        "feedback_status IS NULL OR feedback_status IN ('pending','resolved','stillbad')",
    )
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status','supply','release_note')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status','supply')",
    )
    op.drop_constraint("ck_hub_issues_feedback_status", "hub_issues", type_="check")
    for col in (
        "status_changed_at",
        "self_found",
        "feedback_at",
        "feedback_note",
        "feedback_status",
        "impact_versions",
        "fix_version",
        "release_note",
        "release_notified_at",
        "last_urged_at",
        "urge_count",
    ):
        op.drop_column("hub_issues", col)
