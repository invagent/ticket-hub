"""ticket: add reporter_company / tax_no / tenant / service_level (提单快照字段)

Revision ID: 0024_ticket_reporter_company_fields
Revises: 0023_ticket_handle_hours
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_reporter_fields"
down_revision = "0023_ticket_handle_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("reporter_company", sa.String(256), nullable=True))
    op.add_column("tickets", sa.Column("reporter_tax_no", sa.String(64), nullable=True))
    op.add_column("tickets", sa.Column("reporter_tenant", sa.String(256), nullable=True))
    op.add_column("tickets", sa.Column("service_level", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "service_level")
    op.drop_column("tickets", "reporter_tenant")
    op.drop_column("tickets", "reporter_tax_no")
    op.drop_column("tickets", "reporter_company")
