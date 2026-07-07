"""ADR-0016 P5: 权限双层 — users.role 增加 'knowledge_op'（知识运营）.

Revision ID: 0020_knowledge_op_role
Revises: 0019_owner_split
Create Date: 2026-07-07

知识运营：管 AI 客服对客 skill（反思工作台）+ 飞书 KB/FAQ，够不到内部编排
skill（/admin/skills 保持 require_admin）与主管修正权。反思工作台端点组
从 require_supervisor 放宽为 require_knowledge_op（knowledge_op|supervisor|admin）。
（SQLite 测试 schema 来自 models metadata，不走迁移。）
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_knowledge_op_role"
down_revision: str | Sequence[str] | None = "0019_owner_split"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史 PG 库的 users 表从未建过命名 CHECK（role 约束只在 models metadata /
    # SQLite 测试里），故 DROP IF EXISTS 幂等——存在则替换、不存在则直接建。
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('assignee','supervisor','admin','member','knowledge_op')",
    )


def downgrade() -> None:
    op.execute("UPDATE users SET role='member' WHERE role='knowledge_op'")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('assignee','supervisor','admin','member')",
    )
