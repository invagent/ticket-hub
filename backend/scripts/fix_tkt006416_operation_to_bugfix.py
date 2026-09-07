"""一次性运维脚本：TKT-006416（HUB-000978）人工误判为 Operation，改判回 Bug_fix。

逻辑镜像 POST /api/supervisor/reclassify（app/api/supervisor.py:2143 起）：翻
hub.type、清 Operation 专属字段（reply_content/op_status 等，满足
ck_hub_issues_operation_fields）、更新关联 ticket predicted_type + 写
AgentDecision 审计、按模块负责人是否确定分流（确定 → created 直推 Linear；
不确定 → pending_linear_review 待处理人确认）、写 StatusHistory + record_ticket_action
审计、改判视为已确认分类同步触发 KSM 接管兜底重试。

用法（SIT 容器内）：python3 scripts/fix_tkt006416_operation_to_bugfix.py [--dry-run]
"""

from __future__ import annotations

import argparse

from app.api.history_labels import HUB_TYPE_ZH
from app.db import make_session
from app.models import AgentDecision, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.module_owner import peek_module_owner
from app.services.hub_issues.op_status import (
    default_owner_from_ticket_handler,
    record_ticket_action,
)

_HUB_SHORT_CODE = "HUB-000978"
_NEW_TYPE = "Bug_fix"
_CHANGED_BY = "user:刘伟成"
_REASON = "人工误判 Operation，改判回 Bug_fix"


def main(*, dry_run: bool) -> None:
    db = make_session()
    try:
        hub = db.query(HubIssue).filter_by(short_code=_HUB_SHORT_CODE).one()
        if hub.type != "Operation":
            raise SystemExit(f"{hub.short_code} 当前 type={hub.type!r}，非预期 Operation，中止")
        old_type = hub.type
        old_status = hub.status
        old_zh = HUB_TYPE_ZH.get(old_type, old_type)
        new_zh = HUB_TYPE_ZH.get(_NEW_TYPE, _NEW_TYPE)
        print(f"{hub.short_code}: type {old_type} -> {_NEW_TYPE}, status={hub.status}")

        if dry_run:
            print("[dry-run] 不写入")
            return

        hub.type = _NEW_TYPE
        # Operation → 研发类：清 Operation 专属字段（同 reclassify 分支）。
        hub.reply_content = None
        hub.reply_authored_by = None
        hub.reply_updated_at = None
        hub.op_status = None
        hub.op_handler = None
        hub.op_status_changed_at = None
        hub.op_handler_user_id = None

        linked = (
            db.query(Ticket)
            .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
            .all()
        )
        for tk in linked:
            tk.predicted_type = _NEW_TYPE
            db.add(
                AgentDecision(
                    decision_type="classify_type",
                    subject_type="ticket",
                    subject_id=tk.id,
                    proposal={
                        "predicted_type": _NEW_TYPE,
                        "reason": f"改判 {old_zh}→{new_zh}",
                        "skill": "manual",
                        "human_confirmed": True,
                        "changed_by": _CHANGED_BY,
                    },
                )
            )
        print(f"更新关联 ticket predicted_type: {[t.short_code for t in linked]}")

        if peek_module_owner(db, hub.product_line_code, hub.module) is not None:
            hub.status = "created"
        else:
            hub.status = "pending_linear_review"
            hub.owner_user_id = default_owner_from_ticket_handler(db, hub)
        print(f"hub.status -> {hub.status}")

        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=old_status,
            to_status=hub.status,
            changed_by=_CHANGED_BY,
            reason=f"改判 {old_zh}→{new_zh}: {_REASON}",
        )
        record_ticket_action(
            db,
            hub,
            action="reclassify",
            changed_by=_CHANGED_BY,
            reason=f"改判为 {new_zh}",
        )
        db.commit()
        print(
            "done. 若 status=created 需另外手动触发 push_hub_issue_to_linear(hub.id) 推送 Linear。"
        )
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
