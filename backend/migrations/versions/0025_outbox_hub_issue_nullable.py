"""sync_outbox: make hub_issue_id nullable (工单级批量补料无需已毕业 hub)

Revision ID: 0025_outbox_hub_nullable
Revises: 0024_reporter_fields
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_outbox_hub_nullable"
down_revision = "0024_reporter_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 工单级补料（未毕业 hub 的工单）入 supply outbox 时无 hub_issue_id。
    # batch_alter_table：SQLite 走重建表，PG 走原生 ALTER。
    with op.batch_alter_table("sync_outbox") as batch:
        batch.alter_column("hub_issue_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("sync_outbox") as batch:
        batch.alter_column("hub_issue_id", existing_type=sa.Integer(), nullable=False)
