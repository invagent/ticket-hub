"""ADR-0017 D3: modules.dev_owner_user_id — 模块唯一研发责任人（用户 id 绑死）

Revision ID: 0048_module_dev_owner_user
Revises: 0047_unified_stage
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_module_dev_owner_user"
down_revision: str | Sequence[str] | None = "0047_unified_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill() -> None:
    """dev_owners 首名 → 精确匹配在岗用户姓名 → 写 dev_owner_user_id。匹配不到打印清单，留空。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, product_line_code, name, dev_owners FROM modules "
            "WHERE dev_owner_user_id IS NULL AND dev_owners IS NOT NULL AND dev_owners <> ''"
        )
    ).fetchall()
    unmatched: list[str] = []
    for r in rows:
        names = [p.strip() for p in re.split(r"[、,，]", r.dev_owners or "") if p.strip()]
        if not names:
            continue
        u = bind.execute(
            sa.text(
                "SELECT id FROM users WHERE name = :n AND is_active = true AND deleted_at IS NULL "
                "ORDER BY id LIMIT 1"
            ),
            {"n": names[0]},
        ).fetchone()
        if u is None:
            unmatched.append(f"{r.product_line_code}/{r.name} → {names[0]!r}")
            continue
        bind.execute(
            sa.text("UPDATE modules SET dev_owner_user_id = :u WHERE id = :i"),
            {"u": u.id, "i": r.id},
        )
    if unmatched:
        print("[0048] dev_owner_user_id 未匹配（需在目录管理页手选）：")
        for line in unmatched:
            print("   ", line)


def upgrade() -> None:
    op.add_column(
        "modules",
        sa.Column("dev_owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_modules_dev_owner_user_id", "modules", ["dev_owner_user_id"])
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_modules_dev_owner_user_id", table_name="modules")
    op.drop_column("modules", "dev_owner_user_id")
