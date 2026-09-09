"""ADR-0017 D4: 产品内提单门户 —— tenants / tenant_users / tickets.tenant_user_id / source 'embedded'

Revision ID: 0049_portal_tenants
Revises: 0048_module_dev_owner_user
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_portal_tenants"
down_revision: str | Sequence[str] | None = "0048_module_dev_owner_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("hmac_secret", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "default_product_line_code",
            sa.String(64),
            sa.ForeignKey("product_lines.code"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "tenant_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("external_uid", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("mobile", sa.String(32), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column(
            "customer_identity_id",
            sa.Integer(),
            sa.ForeignKey("customer_identities.id"),
            nullable=True,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "external_uid", name="uq_tenant_users_tenant_uid"),
    )
    op.create_index("ix_tenant_users_tenant", "tenant_users", ["tenant_id"])

    op.add_column(
        "tickets",
        sa.Column("tenant_user_id", sa.Integer(), sa.ForeignKey("tenant_users.id"), nullable=True),
    )
    op.create_index("ix_tickets_tenant_user_id", "tickets", ["tenant_user_id"])

    op.execute(
        sa.text(
            """
            INSERT INTO sources (code, name, is_active)
            VALUES ('embedded', '产品内提单', true)
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_tenant_user_id", table_name="tickets")
    op.drop_column("tickets", "tenant_user_id")
    op.drop_index("ix_tenant_users_tenant", table_name="tenant_users")
    op.drop_table("tenant_users")
    op.drop_table("tenants")
    # source 行保留（可能已有工单引用）
