"""D2-G: catalog tables — modules + features.

Revision ID: 0005_d2_catalog_modules_features
Revises: 0004_d2_product_line_sla
Create Date: 2026-05-09

Centralized registry so admin maintains the canonical Module / Feature
catalog in one place; all other UIs (scope CRUD, user detail, etc.)
read from these to populate dropdowns.

Tables:
    modules(id, product_line_code FK, name, is_active, ...)
        UNIQUE (product_line_code, name)
        Module 是绑 product_line 的（与 assignment_scopes_module 三元唯一约束语义一致）

    features(id, name, is_active, ...)
        UNIQUE (name)
        Feature 跨产品线（与 assignment_scopes_feature 二元唯一约束一致）

Backfill:
    INSERT distinct (product_line_code, module) FROM assignment_scopes_module
        → modules
    INSERT distinct (feature) FROM assignment_scopes_feature
        → features
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_d2_catalog_modules_features"
down_revision: str | Sequence[str] | None = "0004_d2_product_line_sla"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "modules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "product_line_code",
            sa.String(64),
            sa.ForeignKey("product_lines.code"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("product_line_code", "name", name="uq_modules_pl_name"),
    )
    op.create_index("ix_modules_pl_code", "modules", ["product_line_code"])

    op.create_table(
        "features",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # ---- backfill from existing assignment_scopes ------------------------
    op.execute(
        """
        INSERT INTO modules (product_line_code, name, is_active)
        SELECT DISTINCT product_line_code, module, true
        FROM assignment_scopes_module
        ON CONFLICT (product_line_code, name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO features (name, is_active)
        SELECT DISTINCT feature, true
        FROM assignment_scopes_feature
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("features")
    op.drop_index("ix_modules_pl_code", table_name="modules")
    op.drop_table("modules")
