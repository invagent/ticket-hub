"""agent_decisions.decision_type 增加 'classify_module'（AI 产品模块归类审计）.

Revision ID: 0035_agent_decision_classify_module
Revises: 0034_module_classify

module_resolve 写 decision_type='classify_module' 审计行，但 ck_agent_decisions_type
白名单没有它 → CheckViolation 使整个入库归类 commit 回滚（归类值也存不进）。
扩展白名单。Postgres drop+create（SQLite 测试 schema 来自 models metadata）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_agent_decision_classify_module"
down_revision: str | Sequence[str] | None = "0034_module_classify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "decision_type IN ('classify_type','split_ticket','no_split',"
    "'dedup_link','dedup_new','supersede','merge_identity','relink','auto_reply')"
)
_NEW = (
    "decision_type IN ('classify_type','split_ticket','no_split',"
    "'dedup_link','dedup_new','supersede','merge_identity','relink','auto_reply',"
    "'classify_module')"
)


def upgrade() -> None:
    op.drop_constraint("ck_agent_decisions_type", "agent_decisions", type_="check")
    op.create_check_constraint("ck_agent_decisions_type", "agent_decisions", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_agent_decisions_type", "agent_decisions", type_="check")
    op.create_check_constraint("ck_agent_decisions_type", "agent_decisions", _OLD)
