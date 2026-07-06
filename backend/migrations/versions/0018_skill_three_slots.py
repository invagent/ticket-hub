"""skill 三槽版本（ADR-0016 P1）: draft 列 + name 去版本后缀归一.

Revision ID: 0018_skill_three_slots
Revises: 0017_devcollab_hub_fields
Create Date: 2026-07-05

1. skill_prompts 加 draft_md / draft_updated_by / draft_updated_at（draft 槽）。
2. name 归一（业务版本不再进名字，版本走三槽）：
     classify_v2 → classify（classify_v1 行及其历史删除——内容在 git
     prompts/archive/ 存档，DB 不留双轨）
     conflict_detect_v1 → conflict_detect …等 6 个去 _v1 后缀
   skill_prompt_history 的 name 同步 rename，保留修订历史连续性。
SQLite 测试库走 models metadata，不经迁移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_skill_three_slots"
down_revision: str | Sequence[str] | None = "0017_devcollab_hub_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 旧名 → 新名
_RENAMES = {
    "classify_v2": "classify",
    "conflict_detect_v1": "conflict_detect",
    "dedup_v1": "dedup",
    "escalation_classify_v1": "escalation_classify",
    "hub_dedup_v1": "hub_dedup",
    "vision_extract_v1": "vision_extract",
    "escalation_reflect_v1": "escalation_reflect",
}


def upgrade() -> None:
    op.add_column("skill_prompts", sa.Column("draft_md", sa.Text(), nullable=True))
    op.add_column("skill_prompts", sa.Column("draft_updated_by", sa.String(64), nullable=True))
    op.add_column(
        "skill_prompts", sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True)
    )

    conn = op.get_bind()
    # classify_v1 双轨行删除（git prompts/archive/ 有存档）
    conn.execute(sa.text("DELETE FROM skill_prompt_history WHERE name = 'classify_v1'"))
    conn.execute(sa.text("DELETE FROM skill_prompts WHERE name = 'classify_v1'"))
    for old, new in _RENAMES.items():
        # 幂等防撞：目标名已存在（重复跑/手工建过）则删旧行而非撞 unique
        exists = conn.execute(
            sa.text("SELECT 1 FROM skill_prompts WHERE name = :new"), {"new": new}
        ).first()
        if exists:
            conn.execute(sa.text("DELETE FROM skill_prompt_history WHERE name = :old"), {"old": old})
            conn.execute(sa.text("DELETE FROM skill_prompts WHERE name = :old"), {"old": old})
            continue
        conn.execute(
            sa.text("UPDATE skill_prompts SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )
        conn.execute(
            sa.text("UPDATE skill_prompt_history SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        conn.execute(
            sa.text("UPDATE skill_prompts SET name = :old WHERE name = :new"),
            {"old": old, "new": new},
        )
        conn.execute(
            sa.text("UPDATE skill_prompt_history SET name = :old WHERE name = :new"),
            {"old": old, "new": new},
        )
    op.drop_column("skill_prompts", "draft_updated_at")
    op.drop_column("skill_prompts", "draft_updated_by")
    op.drop_column("skill_prompts", "draft_md")
