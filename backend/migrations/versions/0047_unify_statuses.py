"""unify ticket and subtask statuses (two-tier SSOT)

Revision ID: 0047_unify_statuses
Revises: 0046_hub_issue_ticket_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0047_unify_statuses"
down_revision: str | Sequence[str] | None = "0046_hub_issue_ticket_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 工单主状态（tickets.status）平滑清洗对齐为 7 个标准业务状态
    # 历史归档/结束状态统一为 closed
    op.execute(
        "UPDATE tickets SET status = 'closed' WHERE status IN ('done', 'closed', 'rejected', 'superseded')"
    )

    # 历史已发版对齐为 answered
    op.execute(
        "UPDATE tickets SET status = 'answered' WHERE status = 'released'"
    )

    # 活跃工单若关联的 Hub 任务有明确的 op_status，以 op_status 权威对齐
    op.execute(
        """
        UPDATE tickets
        SET status = hub_issues.op_status
        FROM hub_issues
        WHERE tickets.hub_issue_id = hub_issues.id
          AND tickets.status IN ('received', 'in_progress', 'waiting_reply', 'waiting_assign', 'assigned', 'waiting_schedule', 'scheduled', 'code_merged')
          AND hub_issues.op_status IN ('processing', 'reviewing', 'supplementing', 'answered', 'closed', 'exception', 'transferred_return')
        """
    )

    # 其余处于旧初始态/过渡态的工单统一对齐为 processing
    op.execute(
        """
        UPDATE tickets
        SET status = 'processing'
        WHERE status NOT IN ('processing', 'reviewing', 'supplementing', 'answered', 'closed', 'exception', 'transferred_return')
        """
    )

    # 2. 任务执行状态（hub_issues.status）收敛清洗为 4 态：draft / processing / answered / returned
    # 待确认阶段（仅清洗普通初始态 created，严格保留在途闸门审核态 pending_review / pending_linear_review / pending）
    op.execute(
        "UPDATE hub_issues SET status = 'draft' WHERE status = 'created'"
    )

    # 开发中/进行中阶段
    op.execute(
        "UPDATE hub_issues SET status = 'processing' WHERE status IN ('in_progress', 'scheduled')"
    )

    # 已完成/已答复阶段
    op.execute(
        "UPDATE hub_issues SET status = 'answered' WHERE status IN ('answered', 'resolved', 'released', 'done')"
    )

    # 已退回阶段
    op.execute(
        "UPDATE hub_issues SET status = 'returned' WHERE status IN ('returned', 'canceled')"
    )

    # 兜底：其余非法值归入 draft（严格保护在途闸门态）
    op.execute(
        "UPDATE hub_issues SET status = 'draft' WHERE status NOT IN ('draft', 'processing', 'answered', 'returned', 'pending_review', 'pending_linear_review', 'pending')"
    )

    # 3. 清理非应用类（研发类/内部任务）残留的 op_status
    op.execute(
        "UPDATE hub_issues SET op_status = NULL WHERE type IN ('Bug_fix', 'Demand', 'Internal_task') AND op_status IS NOT NULL"
    )


def downgrade() -> None:
    # 逆向降级处理（回滚到历史通用初始态与终态）
    op.execute("UPDATE tickets SET status = 'received' WHERE status = 'processing'")
    op.execute("UPDATE tickets SET status = 'done' WHERE status = 'closed'")
    op.execute("UPDATE hub_issues SET status = 'created' WHERE status = 'draft'")
    op.execute("UPDATE hub_issues SET status = 'in_progress' WHERE status = 'processing'")
    op.execute("UPDATE hub_issues SET status = 'resolved' WHERE status = 'answered'")
