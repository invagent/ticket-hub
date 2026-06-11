"""Evaluation runner for Agent decision quality — D3 classify edition.

Reads a jsonl dataset (one record per line, see schema below) and runs each
record through the classify agent (`classify_payload`, real LLM call via
LLMRouter). Emits a report with overall/per-class accuracy, a confusion
matrix, total cost, and every mismatch for error analysis.

Dataset schema (tests/eval/dataset_v1.jsonl):
    id             unique record id (ksm-001 / sample-001 / syn-005)
    origin         ksm_historical | synthetic_d0 | synthetic_d3
    title, body    ticket text (body may be null)
    product_line   our product line code or null
    module         module name or null
    expected_type  Operation | Bug_fix | Demand | Internal_task
    expected_dedup reserved for D3-E (null for now)
    needs_review   human label still unconfirmed (counted separately)
    note           labeling rationale

Acceptance gate (upgrade_plan v0.5.6 / D3): classify accuracy ≥ 90%.

Usage (from backend/, venv must have app deps + GLM_API_KEY in .env):
    .venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl
    # validate dataset only, no LLM calls:
    .venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl --validate
    # smoke-run on the first N records:
    .venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl --limit 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

# Make the backend `app` importable when running from repo root or backend/.
SCRIPT_DIR = Path(__file__).resolve().parent  # scripts/eval
REPO_ROOT = SCRIPT_DIR.parent.parent  # ticket-hub/
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

VALID_TYPES = ("Operation", "Bug_fix", "Demand", "Internal_task")
REQUIRED_FIELDS = ("id", "title", "expected_type")


def load_dataset(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{lineno}: invalid JSON — {e}") from e
            records.append(rec)
    return records


def validate_dataset(records: list[dict]) -> list[str]:
    """Return a list of problems (empty = dataset OK)."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    for i, rec in enumerate(records, start=1):
        rid = rec.get("id", f"<line {i}>")
        for field in REQUIRED_FIELDS:
            if not rec.get(field):
                problems.append(f"{rid}: missing required field {field!r}")
        if rec.get("expected_type") not in VALID_TYPES:
            problems.append(f"{rid}: expected_type {rec.get('expected_type')!r} invalid")
        if rec.get("id") in seen_ids:
            problems.append(f"{rid}: duplicate id")
        seen_ids.add(rec.get("id", ""))
    return problems


def run_classify_eval(
    records: list[dict], *, limit: int | None = None, provider: str | None = None
) -> dict:
    """Run each record through classify_payload (real LLM). Returns report dict."""
    # Imported here so --validate works without app deps / API keys.
    from app.config import get_settings
    from app.core.llm_router import LLMRouter, LLMRouterError
    from app.services.agents.classify import ClassifyError, classify_payload

    settings = get_settings()
    if not (settings.glm_api_key or settings.dashscope_api_key):
        raise SystemExit(
            "no LLM provider key configured — fill backend/.env first "
            "(or run with --validate for an offline dataset check)."
        )

    router = LLMRouter.from_settings(only=provider)
    subset = records[:limit] if limit else records

    confusion: Counter[tuple[str, str]] = Counter()  # (expected, predicted)
    mismatches: list[dict] = []
    errors: list[dict] = []
    total_cost = 0.0
    confidences: list[float] = []
    t0 = time.monotonic()

    for i, rec in enumerate(subset, start=1):
        expected = rec["expected_type"]
        try:
            result = classify_payload(
                title=rec.get("title"),
                body=rec.get("body"),
                product_line_code=rec.get("product_line"),
                module=rec.get("module"),
                router=router,
            )
        except (ClassifyError, LLMRouterError) as e:
            errors.append({"id": rec["id"], "error": str(e)})
            print(f"  [{i}/{len(subset)}] {rec['id']} ERROR: {e}", file=sys.stderr)
            continue

        total_cost += result.cost_usd
        confidences.append(result.confidence)
        confusion[(expected, result.type)] += 1
        ok = result.type == expected
        if not ok:
            mismatches.append(
                {
                    "id": rec["id"],
                    "title": rec["title"][:80],
                    "expected": expected,
                    "predicted": result.type,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "needs_review": rec.get("needs_review", False),
                }
            )
        mark = "✓" if ok else f"✗ {expected}→{result.type}"
        print(f"  [{i}/{len(subset)}] {rec['id']} {mark} (conf={result.confidence:.2f})")

    elapsed = time.monotonic() - t0
    scored = sum(confusion.values())
    correct = sum(n for (e, p), n in confusion.items() if e == p)

    per_class: dict[str, dict] = {}
    for cls in VALID_TYPES:
        tp = confusion.get((cls, cls), 0)
        support = sum(n for (e, _), n in confusion.items() if e == cls)
        predicted = sum(n for (_, p), n in confusion.items() if p == cls)
        per_class[cls] = {
            "support": support,
            "recall": round(tp / support, 3) if support else None,
            "precision": round(tp / predicted, 3) if predicted else None,
        }

    # Mismatches on needs_review records may be label noise, not model error —
    # report a second accuracy over confirmed labels only.
    error_ids = {e["id"] for e in errors}
    confirmed_ids = {r["id"] for r in subset if not r.get("needs_review")}
    confirmed_scored = len(confirmed_ids - error_ids)
    confirmed_wrong = sum(1 for m in mismatches if m["id"] in confirmed_ids)

    return {
        "dataset_records": len(records),
        "evaluated": len(subset),
        "scored": scored,
        "llm_errors": errors,
        "accuracy": round(correct / scored, 3) if scored else None,
        "accuracy_confirmed_labels_only": (
            round((confirmed_scored - confirmed_wrong) / confirmed_scored, 3)
            if confirmed_scored
            else None
        ),
        "per_class": per_class,
        "confusion": {f"{e}→{p}": n for (e, p), n in sorted(confusion.items())},
        "mismatches": mismatches,
        "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "total_cost_usd": round(total_cost, 6),
        "avg_cost_per_ticket_usd": round(total_cost / scored, 6) if scored else None,
        "elapsed_seconds": round(elapsed, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--validate", action="store_true", help="offline dataset check only")
    parser.add_argument(
        "--provider", default=None, help="restrict to one provider: glm | dashscope"
    )
    parser.add_argument("--limit", type=int, default=None, help="evaluate first N records only")
    parser.add_argument("--threshold", type=float, default=0.9, help="accuracy gate (default 0.9)")
    parser.add_argument("--out", default="/tmp/eval_report.json")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 2

    records = load_dataset(args.dataset)
    problems = validate_dataset(records)
    dist = Counter(r.get("expected_type", "?") for r in records)
    needs_review = sum(1 for r in records if r.get("needs_review"))
    print(
        f"dataset: {args.dataset} — {len(records)} records, "
        f"distribution={dict(dist)}, needs_review={needs_review}"
    )
    if problems:
        for p in problems:
            print(f"  PROBLEM: {p}", file=sys.stderr)
        return 2
    if args.validate:
        print("validation OK (no LLM calls made)")
        return 0

    report = run_classify_eval(records, limit=args.limit, provider=args.provider)
    report["dataset"] = str(args.dataset)
    report["provider"] = args.provider or "default-chain"
    report["threshold"] = args.threshold

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    acc = report["accuracy"]
    print(f"\n=== classify accuracy: {acc} (gate ≥ {args.threshold}) ===")
    print(f"confirmed-labels-only accuracy: {report['accuracy_confirmed_labels_only']}")
    print(f"total cost: ${report['total_cost_usd']} | report: {args.out}")
    if acc is not None and acc < args.threshold:
        print("GATE FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
