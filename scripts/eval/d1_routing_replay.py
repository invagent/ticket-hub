"""D1 verification: routing accuracy replay.

Reads a JSONL fixture (each line = one synthetic ticket with `expected_*`
fields), seeds an in-memory PG-shaped SQLite DB with users + product_lines +
assignment_scopes that match the fixture's `expected_user_ids`, then runs
each fixture record through Router and reports:

    - hit rate (overall)
    - hit rate per scope (module / feature / default_pool)
    - top-N mismatches with rationale

D1 acceptance gate: overall hit rate ≥ 90%.

Usage:
    cd backend
    .venv/bin/python ../scripts/eval/d1_routing_replay.py tests/eval/routing_v1.jsonl
    # Optional: --json /tmp/routing_report.json --threshold 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Make the backend `app` importable when running from repo root or backend/.
SCRIPT_DIR = Path(__file__).resolve().parent  # scripts/eval
REPO_ROOT = SCRIPT_DIR.parent.parent  # ticket-hub/
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import Base  # noqa: E402
from app.models import (  # noqa: E402
    AssignmentScopeFeature,
    AssignmentScopeModule,
    ProductLine,
    User,
    UserPartner,
)
from app.services.routing.router import Router, RouteRequest  # noqa: E402


@dataclass(slots=True)
class Mismatch:
    fixture_id: str
    expected_decision: str
    expected_user_ids: list[int]
    actual_decision: str
    actual_user_ids: list[int]
    rationale: str


@dataclass(slots=True)
class Report:
    total: int = 0
    hits: int = 0
    by_scope: dict[str, list[int]] = field(default_factory=dict)  # scope → [hits, total]
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return (self.hits / self.total) if self.total else 0.0

    def record(
        self,
        *,
        ok: bool,
        scope: str,
        mismatch: Mismatch | None = None,
    ) -> None:
        self.total += 1
        bucket = self.by_scope.setdefault(scope, [0, 0])
        bucket[1] += 1
        if ok:
            self.hits += 1
            bucket[0] += 1
        elif mismatch is not None:
            self.mismatches.append(mismatch)


def _seed_world(db, fixture_records: list[dict]) -> int:
    """Seed users + product_lines + scopes derived from fixture expectations.

    Returns the default_pool user_id (the lowest-numbered "default_pool" user
    referenced in the fixture; ensures the Router can hit it).
    """
    # Collect referenced product lines + users from expected results
    needed_pls: set[str] = set()
    needed_users: set[int] = set()
    module_scopes: set[tuple[int, str, str]] = set()  # (uid, pl, module)
    feature_scopes: set[tuple[int, str]] = set()  # (uid, feature)
    # When `expected_user_ids` has > 1 user AND expected_decision='assigned',
    # interpret them as a partner-group → seed UserPartner edges so Router
    # collapses them to a single routing unit.
    partner_pairs: set[tuple[int, int]] = set()

    for r in fixture_records:
        if r.get("product_line_code"):
            needed_pls.add(r["product_line_code"])
        for uid in r.get("expected_user_ids", []):
            needed_users.add(uid)
        scope = r.get("expected_scope")
        decision = r.get("expected_decision")
        uids = r.get("expected_user_ids") or []
        if decision == "assigned":
            if scope == "module":
                pl = r.get("product_line_code") or "cloud-erp"
                m = r.get("module")
                if m:
                    for uid in uids:
                        module_scopes.add((uid, pl, m))
            elif scope == "feature":
                f = r.get("feature")
                if f:
                    for uid in uids:
                        feature_scopes.add((uid, f))
            # 2+ users sharing the same scope is a partner-group expectation.
            if len(uids) >= 2:
                for i, a in enumerate(uids):
                    for b in uids[i + 1 :]:
                        partner_pairs.add((a, b))

    # Insert product lines
    for code in needed_pls:
        db.add(ProductLine(code=code, name=code))
    db.flush()

    # Insert users (all referenced ids; role doesn't matter for routing)
    for uid in sorted(needed_users):
        db.add(User(id=uid, feishu_uid=f"ou_user_{uid}", name=f"u{uid}", role="assignee"))
    db.flush()

    # Insert scopes
    for uid, pl, m in module_scopes:
        db.add(AssignmentScopeModule(user_id=uid, product_line_code=pl, module=m))
    for uid, f in feature_scopes:
        db.add(AssignmentScopeFeature(user_id=uid, feature=f))
    # Insert symmetric partner edges
    for a, b in partner_pairs:
        db.add(UserPartner(user_id=a, partner_id=b))
        db.add(UserPartner(user_id=b, partner_id=a))
    db.commit()

    # Heuristic for default_pool: every fixture with expected_decision='default_pool'
    # references the same expected_user_ids[0] — that's our pool user.
    pool_candidates = [
        r["expected_user_ids"][0]
        for r in fixture_records
        if r.get("expected_decision") == "default_pool" and r.get("expected_user_ids")
    ]
    return Counter(pool_candidates).most_common(1)[0][0] if pool_candidates else 99


def _classify_actual(decision: str, scope: str) -> str:
    """Map (decision, scope) to a single bucket name for reporting."""
    if decision == "default_pool":
        return "default_pool"
    if decision == "multi_match":
        return f"multi_match:{scope}"
    return scope or "none"


def replay(fixture_path: Path) -> Report:
    records: list[dict] = []
    with fixture_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        pool_user_id = _seed_world(db, records)
        router = Router(db, default_pool_user_id=pool_user_id)
        report = Report()

        for idx, rec in enumerate(records, start=1):
            decision = router.route(
                RouteRequest(
                    ticket_id=idx,
                    source_code="ksm",
                    product_line_code=rec.get("product_line_code"),
                    raw_module=rec.get("module"),
                    raw_feature=rec.get("feature"),
                )
            )
            expected_decision = rec["expected_decision"]
            expected_uids = sorted(rec.get("expected_user_ids", []))
            actual_uids = sorted(decision.assigned_user_ids)
            ok = (
                decision.decision == expected_decision and actual_uids == expected_uids
            )
            scope = _classify_actual(decision.decision, decision.matched_scope)
            mismatch = (
                None
                if ok
                else Mismatch(
                    fixture_id=rec["id"],
                    expected_decision=expected_decision,
                    expected_user_ids=expected_uids,
                    actual_decision=decision.decision,
                    actual_user_ids=actual_uids,
                    rationale=decision.rationale,
                )
            )
            report.record(ok=ok, scope=scope, mismatch=mismatch)
        return report
    finally:
        db.close()
        engine.dispose()


def render_report(report: Report, *, threshold: float) -> str:
    lines = []
    lines.append(f"D1 routing replay — total={report.total} hits={report.hits} "
                 f"hit_rate={report.hit_rate:.1%} threshold={threshold:.0%}")
    lines.append("By scope:")
    for scope, (h, n) in sorted(report.by_scope.items()):
        rate = h / n if n else 0.0
        lines.append(f"  {scope:<25} {h:>3} / {n:<3}  ({rate:.1%})")
    if report.mismatches:
        lines.append(f"Mismatches ({len(report.mismatches)}, top 10):")
        for m in report.mismatches[:10]:
            lines.append(
                f"  [{m.fixture_id}] expected={m.expected_decision}/{m.expected_user_ids} "
                f"actual={m.actual_decision}/{m.actual_user_ids}  {m.rationale}"
            )
    else:
        lines.append("No mismatches. ✅")
    lines.append("")
    verdict = "PASS" if report.hit_rate >= threshold else "FAIL"
    lines.append(f"Verdict: {verdict}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="D1 routing accuracy replay")
    parser.add_argument(
        "fixture",
        type=Path,
        nargs="?",
        default=BACKEND_DIR / "tests" / "eval" / "routing_v1.jsonl",
    )
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--json", dest="json_out", type=Path, default=None)
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"fixture not found: {args.fixture}", file=sys.stderr)
        return 2

    report = replay(args.fixture)
    print(render_report(report, threshold=args.threshold))

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {
                    "total": report.total,
                    "hits": report.hits,
                    "hit_rate": report.hit_rate,
                    "by_scope": {k: {"hits": v[0], "total": v[1]} for k, v in report.by_scope.items()},
                    "mismatches": [
                        {
                            "fixture_id": m.fixture_id,
                            "expected_decision": m.expected_decision,
                            "expected_user_ids": m.expected_user_ids,
                            "actual_decision": m.actual_decision,
                            "actual_user_ids": m.actual_user_ids,
                            "rationale": m.rationale,
                        }
                        for m in report.mismatches
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    return 0 if report.hit_rate >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
