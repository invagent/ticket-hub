"""D3-C: tickets gain LLM-classification fields.

Revision ID: 0006_d3_ticket_classification
Revises: 0005_d2_catalog_modules_features
Create Date: 2026-05-10

Adds three nullable columns on `tickets`:
    predicted_type        — LLM's best guess for hub_issue type
                            ('Operation' | 'Bug_fix' | 'Demand' | 'Internal_task')
    predicted_confidence  — 0.0–1.0
    classified_at         — when the classification was written

When `predicted_confidence >= 0.85` callers (or D3-D conflict-detect)
should use `predicted_type` as the basis for hub_issue creation.
Lower confidence → keep type ambiguous, escalate to supervisor.

We do NOT add an `agent_run_id` FK here yet — D3-A migration will
introduce `agent_runs` and add the FK retroactively.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_d3_ticket_classification"
down_revision: str | Sequence[str] | None = "0005_d2_catalog_modules_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("predicted_type", sa.String(32), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column("predicted_confidence", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column(
        "tickets",
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_tickets_predicted_type",
        "tickets",
        "predicted_type IS NULL OR predicted_type IN "
        "('Operation','Bug_fix','Demand','Internal_task')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tickets_predicted_type", "tickets")
    op.drop_column("tickets", "classified_at")
    op.drop_column("tickets", "predicted_confidence")
    op.drop_column("tickets", "predicted_type")
