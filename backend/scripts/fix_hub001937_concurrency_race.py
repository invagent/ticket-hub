"""一次性运维脚本：修复 HUB-001937（TKT-007063, KSM billNumber R20260825-0775）
人工提交答复后被后台慢异步 auto_answer_operation 并发打回 processing 的竞态。
恢复其 answered/resolved 状态，还原人工真实答复。

用法（SIT 容器内）：python3 scripts/fix_hub001937_concurrency_race.py [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app.db import make_session
from app.models import HubIssue, Ticket
from app.services.hub_issues.op_status import OP_ANSWERED, apply_op_status, record_ticket_action

_HUB_SHORT_CODE = "HUB-001937"
_HANDLER_NAME = "user:苗一琳"
_HANDLER_USER_ID = 42


def main(*, dry_run: bool) -> None:
    db = make_session()
    try:
        hub = db.query(HubIssue).filter_by(short_code=_HUB_SHORT_CODE).one()
        t = db.query(Ticket).filter_by(hub_issue_id=hub.id).first()
        if t is None:
            raise SystemExit(f"未找到 {hub.short_code} 关联的工单")

        print(
            f"Current Hub: status={hub.status}, op_status={hub.op_status}, handler={hub.op_handler}"
        )
        print(f"Ticket cached reply: {t.cached_reply_content[:60]}...")

        if dry_run:
            print("[dry-run] 不执行写入")
            return

        # 还原真实已答复内容
        hub.reply_content = t.cached_reply_content
        hub.reply_is_draft = False
        hub.reply_authored_by = _HANDLER_NAME
        hub.reply_updated_at = datetime.now(UTC)
        hub.status = "resolved"
        hub.op_handler = _HANDLER_NAME
        hub.op_handler_user_id = _HANDLER_USER_ID

        apply_op_status(
            db,
            hub,
            to_status=OP_ANSWERED,
            handler=_HANDLER_NAME,
            reason="修复并发覆盖，还原人工答复已完成状态",
        )
        record_ticket_action(
            db,
            hub,
            action="reply",
            changed_by=_HANDLER_NAME,
            reason="主管人工答复",
        )
        db.commit()
        print("Successfully restored HUB-001937 to answered/resolved!")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
