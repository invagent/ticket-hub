"""D4 第②段: sync_outbox.kind 增加 'supply'（补料回写）.

Revision ID: 0016_v2_outbox_supply_kind
Revises: 0015_v2_holidays
Create Date: 2026-06-26

Extends ck_sync_outbox_kind from ('reply','status') to add 'supply' so the
supervisor "请客户补料" action can enqueue a KSM supplyKsmOrder writeback row.
Postgres drop+create on the check constraint (SQLite test schema comes from
models metadata, not migrations).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_v2_outbox_supply_kind"
down_revision: str | Sequence[str] | None = "0015_v2_holidays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status','supply')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint(
        "ck_sync_outbox_kind",
        "sync_outbox",
        "kind IN ('reply','status')",
    )
