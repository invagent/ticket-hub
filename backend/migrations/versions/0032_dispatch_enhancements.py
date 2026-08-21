"""dispatch enhancements: rule_code, updated_by, sla_levels seed table

Revision ID: 0032_dispatch_enhancements
Revises: 0031_catalog_enhancements
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0032_dispatch_enhancements"
down_revision: str | None = "0031_catalog_enhancements"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # dispatch_rules: add rule_code and updated_by
    op.add_column(
        "dispatch_rules",
        sa.Column("rule_code", sa.String(32), nullable=True),
    )
    op.add_column(
        "dispatch_rules",
        sa.Column("updated_by", sa.String(128), nullable=True),
    )
    op.create_unique_constraint("uq_dispatch_rules_code", "dispatch_rules", ["rule_code"])

    # backfill existing rows with FPYRULE codes (Python loop for DB portability)
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM dispatch_rules WHERE rule_code IS NULL")).fetchall()
    for (rid,) in rows:
        code = f"FPYRULE{rid:04d}"
        bind.execute(
            sa.text("UPDATE dispatch_rules SET rule_code = :code WHERE id = :id"),
            {"code": code, "id": rid},
        )

    # sla_levels: KSM service level code → display name mapping
    sla_table = op.create_table(
        "sla_levels",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer, server_default="0", nullable=False),
    )
    # seed data
    op.bulk_insert(
        sla_table,
        [
            {"code": "22", "name": "标准成功服务（2023版）", "sort_order": 1},
            {"code": "54", "name": "高级成功服务（含定制开发维）", "sort_order": 2},
            {"code": "52", "name": "高级成功服务（仅工单）", "sort_order": 3},
            {"code": "55", "name": "高级成功服务（2023版）", "sort_order": 4},
            {"code": "50", "name": "战略客户绿色通道", "sort_order": 5},
            {"code": "10", "name": "服务期外", "sort_order": 6},
            {"code": "19", "name": "标准成功服务", "sort_order": 7},
        ],
    )


def downgrade() -> None:
    op.drop_table("sla_levels")
    op.drop_constraint("uq_dispatch_rules_code", "dispatch_rules")
    op.drop_column("dispatch_rules", "updated_by")
    op.drop_column("dispatch_rules", "rule_code")
