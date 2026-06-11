"""Dual-write reconciliation: feishu Bitable vs ticket-hub PG.

Cron: every 4h during D1~D6 dual-run window. Diff > 0.1% blocks promotion.

D0: skeleton — exits 0 with a TODO log line. D1 fills in:
  - Pull last 24h tickets from PG
  - Pull same range from Feishu Bitable
  - Compare on (source_code, source_record_id)
  - Output diff report (json/markdown) + exit non-zero on threshold breach
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=4, help="lookback window")
    parser.add_argument("--threshold", type=float, default=0.001, help="failure threshold")
    parser.add_argument("--out", default="/tmp/feishu_pg_diff.json")
    args = parser.parse_args()

    since = datetime.now(UTC) - timedelta(hours=args.hours)

    # D1: replace stub with real comparison
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "since": since.isoformat(),
        "hours": args.hours,
        "threshold": args.threshold,
        "status": "STUB",
        "message": "feishu↔pg diff impl pending — D1",
        "total": 0,
        "diff": 0,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
