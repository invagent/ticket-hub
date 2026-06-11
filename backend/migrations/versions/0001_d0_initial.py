"""D0 initial schema: sources / product_lines / users.

Revision ID: 0001_d0_initial
Revises:
Create Date: 2026-05-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_d0_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )
    op.create_index("ix_sources_code", "sources", ["code"])

    op.create_table(
        "product_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("code", name="uq_product_lines_code"),
    )
    op.create_index("ix_product_lines_code", "product_lines", ["code"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feishu_uid", sa.String(64), nullable=False),
        sa.Column("employee_no", sa.String(64), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("mobile", sa.String(32), nullable=True),
        sa.Column("ksm_account", sa.String(64), nullable=True),
        sa.Column("zhichi_agent_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("feishu_uid", name="uq_users_feishu_uid"),
    )
    op.create_index("ix_users_feishu_uid", "users", ["feishu_uid"])
    op.create_index("ix_users_employee_no", "users", ["employee_no"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_ksm_account", "users", ["ksm_account"])
    op.create_index("ix_users_zhichi_agent_id", "users", ["zhichi_agent_id"])

    # Seed required sources — FK target for tickets and customer_identities.
    # ON CONFLICT DO NOTHING makes this safe to re-run on existing databases.
    op.execute(sa.text("""
            INSERT INTO sources (code, name, is_active)
            VALUES
                ('ksm',     'KSM',    true),
                ('zhichi',  '智齿',   true),
                ('zammad',  'Zammad', true),
                ('linear',  'Linear', true)
            ON CONFLICT (code) DO NOTHING
            """))


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("product_lines")
    op.drop_table("sources")
