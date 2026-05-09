"""D2-C: per-product-line SLA thresholds on product_lines.

Revision ID: 0004_d2_product_line_sla
Revises: 0003_d2_materialized_metrics
Create Date: 2026-05-09

Adds two nullable INT columns:
    sla_reply_hours   — first-response threshold for tickets in this line
    sla_resolve_hours — first-response threshold for hub_issues in this line

NULL on either column means "fall back to the SLAWatcher built-in default".
Non-NULL overrides per-product. Admin sets/clears via PATCH
/api/admin/product-lines/{code}.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_d2_product_line_sla"
down_revision: str | Sequence[str] | None = "0003_d2_materialized_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_lines",
        sa.Column("sla_reply_hours", sa.Integer, nullable=True),
    )
    op.add_column(
        "product_lines",
        sa.Column("sla_resolve_hours", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_lines", "sla_resolve_hours")
    op.drop_column("product_lines", "sla_reply_hours")
