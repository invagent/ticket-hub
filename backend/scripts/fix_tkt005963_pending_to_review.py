"""一次性运维脚本: 纠正 TKT-005963 gate① bypass bug 的存量 hub。

背景: 修复前 dispatch_missed 提前分流曾绕过闸门①, 让分派缺人的研发类工单
直接进 pending(Linear推送待人工队列)而非 pending_review(待确认分类队列)。
代码已修(commit 17738b0)。本脚本把修复前进错队列的存量 hub 移回 pending_review,
使其进入分类确认队列可被确认/改判。

判据: status='pending' + 研发类 + agent:dispatch 转入 + 未推 Linear(linear_uuid 空)。
幂等: 已是 pending_review 或已推 Linear 的不动。
用法(SIT 容器内): python3 scripts/fix_tkt005963_pending_to_review.py
"""

from __future__ import annotations

from app.db import make_session
from app.models import HubIssue, StatusHistory


def main() -> None:
    db = make_session()
    try:
        candidates = (
            db.query(HubIssue)
            .filter(
                HubIssue.status == "pending",
                HubIssue.type.in_(["Bug_fix", "Demand"]),
                HubIssue.deleted_at.is_(None),
            )
            .all()
        )
        fixed = 0
        for h in candidates:
            via_dispatch = (
                db.query(StatusHistory)
                .filter_by(
                    entity_type="hub_issue",
                    entity_id=h.id,
                    to_status="pending",
                    changed_by="agent:dispatch",
                )
                .first()
            )
            if via_dispatch is None:
                continue
            if h.linear_uuid is not None:
                print(f"skip {h.short_code}: already pushed to linear")
                continue
            prev = h.status
            h.status = "pending_review"
            db.add(
                StatusHistory(
                    entity_type="hub_issue",
                    entity_id=h.id,
                    from_status=prev,
                    to_status="pending_review",
                    changed_by="system:gate_fix",
                    reason="TKT-005963 gate1 bypass fix: 回退进分类确认队列",
                )
            )
            fixed += 1
            print(f"fixed {h.short_code}: {prev} -> pending_review")
        db.commit()
        print(f"total fixed: {fixed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
