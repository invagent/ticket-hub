"""ADR-0017 stage 对账脚本：报告并可选修正「存储 stage ≠ 派生 stage」的漂移。

用法：
    python scripts/reconcile_stage.py            # 只报告
    python scripts/reconcile_stage.py --fix      # 修正并写 status_history(system:stage_reconcile)
    python scripts/reconcile_stage.py --limit 500
退出码：0=无漂移或已修正；2=有漂移未修正。
"""

from __future__ import annotations

import argparse
import sys

from app.db import make_session
from app.services.state.reconcile import reconcile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    db = make_session()
    try:
        report = reconcile(db, fix=args.fix, limit=args.limit)
        if args.fix:
            db.commit()
    finally:
        db.close()

    print(f"checked tickets={report.tickets_checked} hubs={report.hubs_checked}")
    for d in report.drifts or []:
        print(f"drift: {d.entity}#{d.entity_id} stored={d.stored!r} derived={d.derived!r}")
    print(f"drifts={len(report.drifts or [])} fixed={report.fixed}")
    if report.drifts and not args.fix:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
