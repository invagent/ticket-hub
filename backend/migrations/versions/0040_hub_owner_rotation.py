"""hub_issues 加责任人字段 + modules 加研发责任人轮询游标.

Revision ID: 0040_hub_owner_rotation
Revises: 0039_ticket_diagnosis_flag

责任人字段（hub_issues.owner_user_id）：默认=处理人（ticket.handler_user_id），推
Linear（直连或飞书 webhook 出口）后=推送时确定的模块负责人。与既有
hub_issues.assigned_user_id（入库路由责任人，语义固定不变）是两个不同字段。新功能
字段，不回填历史数据，老 hub 留 NULL。

轮询游标（modules.dev_owner_rotation_cursor）：dev_owners 配置多人时按顺序轮询分配，
游标落在 modules 行本身（与 dev_owners 同行同事务，人员增减不会产生两表不同步）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_hub_owner_rotation"
down_revision: str | Sequence[str] | None = "0039_ticket_diagnosis_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hub_issues_owner_user_id", "hub_issues", ["owner_user_id"])
    op.add_column(
        "modules",
        sa.Column(
            "dev_owner_rotation_cursor", sa.Integer(), server_default="0", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("modules", "dev_owner_rotation_cursor")
    op.drop_index("ix_hub_issues_owner_user_id", table_name="hub_issues")
    op.drop_column("hub_issues", "owner_user_id")
