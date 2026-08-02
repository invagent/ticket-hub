"""attachment: add queued status + download retry columns

Revision ID: 0022_attachment_queued
Revises: 0021_operation_status_machine
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_attachment_queued"
down_revision = "0021_operation_status_machine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("attachments", sa.Column("last_error", sa.String(512), nullable=True))
    # 升级 CHECK 约束加 'queued'
    op.drop_constraint("ck_attachments_vision_status", "attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_vision_status",
        "attachments",
        "vision_status IN ('pending','queued','extracted','skipped','failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attachments_vision_status", "attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_vision_status",
        "attachments",
        "vision_status IN ('pending','extracted','skipped','failed')",
    )
    op.drop_column("attachments", "last_error")
    op.drop_column("attachments", "download_attempts")
