"""xlsx → PG migration (decision R2).

One-time D0 dry-run script. Reads docs/KSM协同基础资料.xlsx from the
feishu-workorder repo and emits a diff report against the current PG state.

D0: stub — prints "would migrate N rows" without writing.
D1: implements the diff. D6: physical delete of the xlsx.

Usage:
  python scripts/migrate/xlsx_migrate.py --xlsx <path> [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="actually write to PG (default: dry-run)")
    args = parser.parse_args()

    if not args.xlsx.exists():
        print(f"xlsx not found: {args.xlsx}", file=sys.stderr)
        return 2

    print(f"[stub] would read {args.xlsx} and {'WRITE' if args.apply else 'dry-run'} to PG")
    print("[stub] D1 will implement openpyxl read + diff vs PG + apply path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
