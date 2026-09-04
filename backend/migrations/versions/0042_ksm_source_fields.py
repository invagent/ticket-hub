"""tickets 加 KSM 源字段落库（提单产品线/模块、联系人、关单节点）.

Revision ID: 0042_ksm_source_fields
Revises: 0041_dispatch_log_ticket_id

新增：
  ksm_reporter_product_line / ksm_reporter_module — KSM 原始 product.name/module.name
    （未经归类映射的 KSM 侧原样值，跟已被归类链覆盖的 product_line_code/module 区分）
  ksm_linkman / ksm_contact_mobile / ksm_contact_email — customerInfo.linkman/mobile/email
    （客户公司联系人，跟 reporter「反馈人」feedbackUser 语义不同）
  ksm_close_node_id / ksm_close_node_name / ksm_close_node_status — closereason.id/name/status

复用现有 source_status 列承接 KSM status（1=受理/2=处理），不新增列。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_ksm_source_fields"
down_revision: str | Sequence[str] | None = "0041_dispatch_log_ticket_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("ksm_reporter_product_line", sa.String(128), nullable=True))
    op.add_column("tickets", sa.Column("ksm_reporter_module", sa.String(128), nullable=True))
    op.add_column("tickets", sa.Column("ksm_linkman", sa.String(128), nullable=True))
    op.add_column("tickets", sa.Column("ksm_contact_mobile", sa.String(32), nullable=True))
    op.add_column("tickets", sa.Column("ksm_contact_email", sa.String(128), nullable=True))
    op.add_column("tickets", sa.Column("ksm_close_node_id", sa.String(64), nullable=True))
    op.add_column("tickets", sa.Column("ksm_close_node_name", sa.String(128), nullable=True))
    op.add_column("tickets", sa.Column("ksm_close_node_status", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "ksm_close_node_status")
    op.drop_column("tickets", "ksm_close_node_name")
    op.drop_column("tickets", "ksm_close_node_id")
    op.drop_column("tickets", "ksm_contact_email")
    op.drop_column("tickets", "ksm_contact_mobile")
    op.drop_column("tickets", "ksm_linkman")
    op.drop_column("tickets", "ksm_reporter_module")
    op.drop_column("tickets", "ksm_reporter_product_line")
