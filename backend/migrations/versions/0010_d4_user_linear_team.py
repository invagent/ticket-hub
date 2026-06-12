"""D4: users.linear_team_id — per-assignee Linear team routing.

Revision ID: 0010_d4_user_linear_team
Revises: 0009_d3_e_ticket_embeddings
Create Date: 2026-06-12

Linear push routes a Bug_fix/Demand issue to the team of its assignee. The
team is resolved from the assignee's Linear identity during the email-matched
user sync and denormalized here so push stays a single lookup (no per-push
Linear query). NULL → push falls back to the default LINEAR_TEAM_ID; group
accounts (no email) stay NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_d4_user_linear_team"
down_revision: str | Sequence[str] | None = "0009_d3_e_ticket_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("linear_team_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "linear_team_id")
