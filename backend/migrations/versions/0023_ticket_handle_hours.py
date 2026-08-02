"""ticket: add handle_hours + sla_standard_hours for analytics dashboard

Revision ID: 0023_ticket_handle_hours
Revises: 0022_attachment_queued
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_ticket_handle_hours"
down_revision = "0022_attachment_queued"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("handle_hours", sa.Numeric(7, 2), nullable=True))
    op.add_column("tickets", sa.Column("sla_standard_hours", sa.Numeric(7, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "sla_standard_hours")
    op.drop_column("tickets", "handle_hours")
