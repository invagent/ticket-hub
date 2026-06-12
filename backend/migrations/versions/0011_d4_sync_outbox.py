"""D4: sync_outbox — outbound write queue for cascade fan-out (ADR-0007).

Revision ID: 0011_d4_sync_outbox
Revises: 0010_d4_user_linear_team
Create Date: 2026-06-12

Producers (D4): reply_sync / status_cascade enqueue per-sourced-ticket rows.
Consumers (D5): per-source senders drain pending rows. Until D5 lands, rows
accumulate as 'pending' — that decoupling is deliberate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_d4_sync_outbox"
down_revision: str | Sequence[str] | None = "0010_d4_user_linear_team"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_outbox",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "target_source_code", sa.String(32), sa.ForeignKey("sources.code"), nullable=False
        ),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("source_ticket_id", sa.String(128), nullable=False),
        sa.Column("hub_issue_id", sa.Integer, sa.ForeignKey("hub_issues.id"), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('reply','status')", name="ck_sync_outbox_kind"),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed','skipped')", name="ck_sync_outbox_status"
        ),
    )
    op.create_index("ix_sync_outbox_drain", "sync_outbox", ["status", "created_at"])
    op.create_index("ix_sync_outbox_ticket", "sync_outbox", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_outbox_ticket", table_name="sync_outbox")
    op.drop_index("ix_sync_outbox_drain", table_name="sync_outbox")
    op.drop_table("sync_outbox")
