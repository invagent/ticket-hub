"""一次性运维脚本：纠正 HUB-001173（TKT-006616, KSM billNumber R20260901-3181）
误判为 Demand 的分类，改回 Operation。

逻辑镜像 PATCH /api/hub-issues/{id}/attributes 里 type 改判分支（app/api/
hub_issues.py:655 起）：翻 hub.type、清 Linear/研发专属字段不适用（Operation
不需要清，进入 Operation 分支本身就是设置 Operation 状态）、更新关联 ticket
predicted_type + 写 AgentDecision 审计、status 规整（进 Operation：status=created
+ apply_op_status(OP_PROCESSING) + dispatch_handler 分派处理人）、写
StatusHistory + record_ticket_action 审计。

用法（SIT 容器内）：python3 scripts/fix_hub001173_demand_to_operation.py [--dry-run]
"""

from __future__ import annotations

import argparse

from app.db import make_session
from app.models import AgentDecision, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.dispatch import dispatch_handler
from app.services.hub_issues.op_status import (
    OP_PROCESSING,
    apply_op_status,
    record_ticket_action,
    set_hub_tickets_handler,
)

_HUB_SHORT_CODE = "HUB-001173"
_CHANGED_BY = "user:刘伟成"


def main(*, dry_run: bool) -> None:
    db = make_session()
    try:
        hub = db.query(HubIssue).filter_by(short_code=_HUB_SHORT_CODE).one()
        if hub.type != "Demand":
            raise SystemExit(f"{hub.short_code} 当前 type={hub.type!r}，非预期 Demand，中止")
        old = hub.type
        print(f"{hub.short_code}: type {old} -> Operation, status={hub.status}")

        if dry_run:
            print("[dry-run] 不写入")
            return

        hub.type = "Operation"

        linked = (
            db.query(Ticket)
            .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
            .all()
        )
        for tk in linked:
            tk.predicted_type = "Operation"
            db.add(
                AgentDecision(
                    decision_type="classify_type",
                    subject_type="ticket",
                    subject_id=tk.id,
                    proposal={
                        "predicted_type": "Operation",
                        "reason": f"手动修改 {old}→Operation",
                        "skill": "manual",
                        "human_confirmed": True,
                        "changed_by": _CHANGED_BY,
                    },
                )
            )
        print(f"更新关联 ticket predicted_type: {[t.short_code for t in linked]}")

        hub.status = "created"
        apply_op_status(
            db,
            hub,
            to_status=OP_PROCESSING,
            handler="agent",
            reason=f"手动修改 {old}→运营，回炉答复链",
        )
        db.flush()
        dr = dispatch_handler(db, hub)
        if dr.user_id is not None:
            hub.op_handler_user_id = dr.user_id
            set_hub_tickets_handler(db, hub, dr.user_id)
        print(f"dispatch: user_id={dr.user_id}")

        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=hub.status,
            to_status=hub.status,
            changed_by=_CHANGED_BY,
            reason=f"修改工单参数: 类型 {old}→Operation",
        )
        record_ticket_action(
            db,
            hub,
            action="edit_attributes",
            changed_by=_CHANGED_BY,
            reason=f"类型 {old}→Operation",
        )
        db.commit()
        print("done.")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
