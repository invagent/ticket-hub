"""D3-E: ticket_embeddings — dedup recall vectors.

Revision ID: 0009_d3_e_ticket_embeddings
Revises: 0008_merge_system_settings
Create Date: 2026-06-11

One embedding per ticket (PK = ticket_id). Vector stored as JSON and compared
with Python cosine over a bounded recent pool — deliberate non-pgvector choice
at current ticket volume (hundreds/day); migrate to pgvector + ANN index if
the pool scan ever shows up in profiles.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_d3_e_ticket_embeddings"
down_revision: str | Sequence[str] | None = "0008_merge_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_embeddings",
        sa.Column(
            "ticket_id",
            sa.Integer,
            sa.ForeignKey("tickets.id"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("dim", sa.Integer, nullable=False),
        sa.Column("vector", sa.JSON, nullable=False),
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
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ticket_embeddings")
