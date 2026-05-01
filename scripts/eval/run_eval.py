"""Evaluation runner for Agent decision quality.

Reads a jsonl dataset (one record per line) and runs each item through the
configured Agent stack. Emits a report with per-decision-type accuracy +
top-N error cases.

D0: skeleton runs the dataset stub and prints per-record echo. D3 wires:
  - Type Classify accuracy (4 classes)
  - Conflict Detect decision matrix
  - Dedup recall@k
  - LLM provider comparison (--provider=openai|deepseek|glm|anthropic)

Usage:
  python scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--out", default="/tmp/eval_report.json")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 2

    records: list[dict] = []
    with args.dataset.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    type_counter: Counter[str] = Counter()
    for rec in records:
        if "expected_type" in rec:
            type_counter[rec["expected_type"]] += 1

    report = {
        "dataset": str(args.dataset),
        "provider": args.provider,
        "total_records": len(records),
        "label_distribution": dict(type_counter),
        "status": "STUB",
        "message": "Agent eval impl pending — D3",
    }
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
