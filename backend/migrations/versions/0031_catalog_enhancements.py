"""catalog enhancements: product_line category + module status/owners

Revision ID: 0031_catalog_enhancements
Revises: 0030_ticket_handler
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0031_catalog_enhancements"
down_revision: str | None = "0030_ticket_handler"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # product_lines: add category column
    op.add_column(
        "product_lines",
        sa.Column("category", sa.String(64), nullable=True),
    )

    # modules: add status, product_owner, dev_owners, updated_by
    op.add_column(
        "modules",
        sa.Column("status", sa.String(16), server_default="enabled", nullable=False),
    )
    op.add_column(
        "modules",
        sa.Column("product_owner", sa.String(256), nullable=True),
    )
    op.add_column(
        "modules",
        sa.Column("dev_owners", sa.String(512), nullable=True),
    )
    op.add_column(
        "modules",
        sa.Column("updated_by", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("modules", "updated_by")
    op.drop_column("modules", "dev_owners")
    op.drop_column("modules", "product_owner")
    op.drop_column("modules", "status")
    op.drop_column("product_lines", "category")
