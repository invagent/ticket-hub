"""sync_outbox.kind 增加 'return'（退回 KSM 出站写回）.

Revision ID: 0036_outbox_return_kind
Revises: 0035_ad_classify_module

处理人在工单详情页把 KSM 来源工单退回到 KSM 重新分派（returnKsmOrder）。
Extends ck_sync_outbox_kind 加 'return'。Postgres drop+create（SQLite 测试
schema 来自 models metadata，非迁移）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_outbox_return_kind"
down_revision: str | Sequence[str] | None = "0035_ad_classify_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "kind IN ('reply','status','supply','release_note','progress_note')"
_NEW = "kind IN ('reply','status','supply','release_note','progress_note','return')"


def upgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint("ck_sync_outbox_kind", "sync_outbox", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_sync_outbox_kind", "sync_outbox", type_="check")
    op.create_check_constraint("ck_sync_outbox_kind", "sync_outbox", _OLD)
