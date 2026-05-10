"""D3-A: agent_decisions — minimal audit table for Agent decisions.

Revision ID: 0007_d3_a_agent_decisions
Revises: 0006_d3_ticket_classification
Create Date: 2026-05-10

One row per Agent decision (classify / split / dedup / ...). Supervisor revert
flips status='reverted' + sets reverted_at/reverted_by/revert_reason.

Intentionally minimal:
  - No agent_runs (LLM call telemetry covered by structured logs already)
  - No agent_decision_targets (multi-target cases serialize to proposal.related_ids)
  - No PII tables yet (deferred)

Schema:
    id              INT PK
    decision_type   VARCHAR(32)  — classify_type / split_ticket / no_split /
                                   dedup_link / dedup_new / supersede /
                                   merge_identity / relink / auto_reply
    subject_type    VARCHAR(16)  — 'ticket' | 'hub_issue' (indexed with subject_id)
    subject_id      INT
    proposal        JSON         — decision payload (model, predicted_type,
                                   confidence, reason, related_ids, ...)
    status          VARCHAR(16)  — 'executed' | 'reverted' (default executed)
    executed_at     TIMESTAMPTZ  — defaults to now()
    reverted_at     TIMESTAMPTZ  — set when status flips to 'reverted'
    reverted_by     VARCHAR(64)  — supervisor identity
    revert_reason   TEXT
    created_at      TIMESTAMPTZ
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_d3_a_agent_decisions"
down_revision: str | Sequence[str] | None = "0006_d3_ticket_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DECISION_TYPES = (
    "classify_type",
    "split_ticket",
    "no_split",
    "dedup_link",
    "dedup_new",
    "supersede",
    "merge_identity",
    "relink",
    "auto_reply",
)


def upgrade() -> None:
    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("decision_type", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.Integer, nullable=False),
        sa.Column("proposal", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="executed"),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_by", sa.String(64), nullable=True),
        sa.Column("revert_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_type IN (" + ",".join(f"'{t}'" for t in _DECISION_TYPES) + ")",
            name="ck_agent_decisions_type",
        ),
        sa.CheckConstraint(
            "subject_type IN ('ticket','hub_issue')",
            name="ck_agent_decisions_subject_type",
        ),
        sa.CheckConstraint(
            "status IN ('executed','reverted')",
            name="ck_agent_decisions_status",
        ),
        sa.CheckConstraint(
            "(status='executed' AND reverted_at IS NULL) "
            "OR (status='reverted' AND reverted_at IS NOT NULL)",
            name="ck_agent_decisions_reverted_consistency",
        ),
    )
    op.create_index(
        "ix_agent_decisions_subject",
        "agent_decisions",
        ["subject_type", "subject_id"],
    )
    op.create_index(
        "ix_agent_decisions_type_created",
        "agent_decisions",
        ["decision_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decisions_type_created", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_subject", table_name="agent_decisions")
    op.drop_table("agent_decisions")
