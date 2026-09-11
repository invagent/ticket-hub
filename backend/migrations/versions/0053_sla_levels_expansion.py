"""sla_levels table expansion and primary key upgrade

Revision ID: 0053_sla_levels_expansion
Revises: 0052_ticket_process_stage
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_sla_levels_expansion"
down_revision: str | Sequence[str] | None = "0052_ticket_process_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 移除 code 主键约束
    op.drop_constraint("sla_levels_pkey", "sla_levels", type_="primary")

    # 2. 新增 id 列（SEVERLEVEL#### 流水号）及扩展字段
    op.add_column("sla_levels", sa.Column("id", sa.String(32), nullable=True))
    op.add_column(
        "sla_levels",
        sa.Column(
            "issue_levels",
            sa.String(128),
            server_default="P0、P1、P2、P3",
            nullable=False,
        ),
    )
    op.add_column(
        "sla_levels",
        sa.Column(
            "issue_types",
            sa.String(128),
            server_default="不限",
            nullable=False,
        ),
    )
    op.add_column(
        "sla_levels",
        sa.Column(
            "sla_hours",
            sa.Numeric(10, 2),
            server_default="40.0",
            nullable=False,
        ),
    )
    op.add_column(
        "sla_levels",
        sa.Column(
            "source_system",
            sa.String(64),
            server_default="KSM",
            nullable=False,
        ),
    )
    op.add_column(
        "sla_levels",
        sa.Column(
            "source_system_field",
            sa.String(64),
            server_default="serviceLevel",
            nullable=False,
        ),
    )
    op.add_column("sla_levels", sa.Column("source_system_code", sa.String(64), nullable=True))
    op.add_column("sla_levels", sa.Column("updated_by", sa.String(128), nullable=True))
    op.add_column(
        "sla_levels",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 3. 回填存量 KSM 数据：按 sort_order 分配 SEVERLEVEL0001 ~ SEVERLEVEL0007
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT code FROM sla_levels ORDER BY sort_order ASC")).fetchall()
    for idx, (code_val,) in enumerate(rows, start=1):
        level_id = f"SEVERLEVEL{idx:04d}"
        bind.execute(
            sa.text(
                """
                UPDATE sla_levels
                SET id = :level_id,
                    source_system_code = :code_val,
                    issue_levels = 'P0、P1、P2、P3',
                    issue_types = '不限',
                    sla_hours = 40.0,
                    source_system = 'KSM',
                    source_system_field = 'serviceLevel',
                    updated_by = '系统初始化'
                WHERE code = :code_val
                """
            ),
            {"level_id": level_id, "code_val": code_val},
        )

    # 4. 将 id 设为非空并设为主键
    op.alter_column("sla_levels", "id", nullable=False)
    op.create_primary_key("sla_levels_pkey", "sla_levels", ["id"])

    # 5. 迁移智齿 4 档服务等级字典数据（SEVERLEVEL0008 ~ SEVERLEVEL0011）
    zhichi_levels = [
        ("SEVERLEVEL0008", "0", "普通", 8),
        ("SEVERLEVEL0009", "1", "标准", 9),
        ("SEVERLEVEL0010", "2", "重要", 10),
        ("SEVERLEVEL0011", "3", "紧急", 11),
    ]
    for lid, code_val, name_val, sort_val in zhichi_levels:
        bind.execute(
            sa.text(
                """
                INSERT INTO sla_levels (
                    id, code, name, sort_order, issue_levels, issue_types,
                    sla_hours, source_system, source_system_field, source_system_code,
                    updated_by, updated_at
                )
                VALUES (
                    :id, :code, :name, :sort_order, 'P0、P1、P2、P3', '不限',
                    40.0, '智齿', 'ticket_level', :code,
                    '系统初始化', NOW()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": lid, "code": code_val, "name": name_val, "sort_order": sort_val},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM sla_levels WHERE source_system = '智齿'"))
    op.drop_constraint("sla_levels_pkey", "sla_levels", type_="primary")
    op.create_primary_key("sla_levels_pkey", "sla_levels", ["code"])
    op.drop_column("sla_levels", "updated_at")
    op.drop_column("sla_levels", "updated_by")
    op.drop_column("sla_levels", "source_system_code")
    op.drop_column("sla_levels", "source_system_field")
    op.drop_column("sla_levels", "source_system")
    op.drop_column("sla_levels", "sla_hours")
    op.drop_column("sla_levels", "issue_types")
    op.drop_column("sla_levels", "issue_levels")
    op.drop_column("sla_levels", "id")
