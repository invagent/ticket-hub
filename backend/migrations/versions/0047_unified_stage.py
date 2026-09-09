"""ADR-0017: unified stage on tickets/hub_issues + stage history entity types

Revision ID: 0047_unified_stage
Revises: 0046_hub_issue_ticket_id
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision: str = "0047_unified_stage"
down_revision: str | Sequence[str] | None = "0046_hub_issue_ticket_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 1000


def _backfill() -> None:
    """用与运行时相同的派生函数回填存量 stage（分批，幂等：只填 NULL）。"""
    from app.services.state.stage import derive_hub_stage, derive_ticket_stage

    bind = op.get_bind()
    now = sa.func.now()

    # hubs
    hubs = bind.execute(
        sa.text(
            "SELECT id, type, status, op_status, linear_status FROM hub_issues WHERE stage IS NULL"
        )
    ).fetchall()
    hub_stage: dict[int, str] = {}
    for r in hubs:
        hub_stage[r.id] = derive_hub_stage(
            SimpleNamespace(
                type=r.type, status=r.status, op_status=r.op_status, linear_status=r.linear_status
            )
        )
    ids = list(hub_stage)
    for i in range(0, len(ids), _BATCH):
        chunk = ids[i : i + _BATCH]
        for hid in chunk:
            bind.execute(
                sa.text("UPDATE hub_issues SET stage=:s, stage_changed_at=:t WHERE id=:i"),
                {"s": hub_stage[hid], "t": None, "i": hid},
            )
    bind.execute(
        sa.text(
            "UPDATE hub_issues SET stage_changed_at = updated_at WHERE stage IS NOT NULL AND stage_changed_at IS NULL"
        )
    )

    # tickets (join hub fields)
    rows = bind.execute(
        sa.text(
            "SELECT t.id, t.status, t.type, t.predicted_type, t.hub_issue_id, "
            "h.type AS h_type, h.status AS h_status, h.op_status AS h_op, h.linear_status AS h_lin "
            "FROM tickets t LEFT JOIN hub_issues h ON h.id = t.hub_issue_id WHERE t.stage IS NULL"
        )
    ).fetchall()
    for i in range(0, len(rows), _BATCH):
        for r in rows[i : i + _BATCH]:
            hub = None
            if r.hub_issue_id is not None and r.h_status is not None:
                hub = SimpleNamespace(
                    type=r.h_type, status=r.h_status, op_status=r.h_op, linear_status=r.h_lin
                )
            ticket = SimpleNamespace(
                status=r.status,
                type=r.type,
                predicted_type=r.predicted_type,
                hub_issue_id=r.hub_issue_id,
            )
            bind.execute(
                sa.text("UPDATE tickets SET stage=:s WHERE id=:i"),
                {"s": derive_ticket_stage(ticket, hub), "i": r.id},
            )
    bind.execute(
        sa.text(
            "UPDATE tickets SET stage_changed_at = updated_at WHERE stage IS NOT NULL AND stage_changed_at IS NULL"
        )
    )
    del now


def upgrade() -> None:
    op.add_column("tickets", sa.Column("stage", sa.String(32), nullable=True))
    op.add_column(
        "tickets", sa.Column("stage_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_tickets_stage", "tickets", ["stage"])
    op.add_column("hub_issues", sa.Column("stage", sa.String(32), nullable=True))
    op.add_column(
        "hub_issues", sa.Column("stage_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_hub_issues_stage", "hub_issues", ["stage"])

    # PG 历史库可能从未建过这个命名 CHECK（0020 教训）→ IF EXISTS
    op.execute("ALTER TABLE status_history DROP CONSTRAINT IF EXISTS ck_status_history_entity")
    op.create_check_constraint(
        "ck_status_history_entity",
        "status_history",
        "entity_type IN ('ticket','hub_issue','ticket_stage','hub_stage')",
    )

    _backfill()


def downgrade() -> None:
    op.execute("DELETE FROM status_history WHERE entity_type IN ('ticket_stage','hub_stage')")
    op.execute("ALTER TABLE status_history DROP CONSTRAINT IF EXISTS ck_status_history_entity")
    op.create_check_constraint(
        "ck_status_history_entity", "status_history", "entity_type IN ('ticket','hub_issue')"
    )
    op.drop_index("ix_hub_issues_stage", table_name="hub_issues")
    op.drop_column("hub_issues", "stage_changed_at")
    op.drop_column("hub_issues", "stage")
    op.drop_index("ix_tickets_stage", table_name="tickets")
    op.drop_column("tickets", "stage_changed_at")
    op.drop_column("tickets", "stage")
