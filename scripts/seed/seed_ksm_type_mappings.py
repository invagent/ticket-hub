"""Seed ksm_issue_type_mappings from yaml.

Upserts by (ksm_category, ksm_subcategory) — the table's UNIQUE constraint.
Re-running with the same yaml is a no-op; runs with edited rows update
target_module / target_feature / classification_hint / notes / is_active
in place. Rows present in DB but absent from yaml are NOT auto-deleted
(safety: avoid wiping production manually-curated rows when running with
a stale yaml). Use --prune to opt into deletion of out-of-yaml rows.

Yaml schema (matches backend/config/mappings/ksm_issue_types.yaml):

    mappings:
      - ksm_category: "财务-应付审核"
        ksm_subcategory: ~                # null → top-level category
        product_line_code: cloud-erp
        target_module: 应付管理
        target_feature: ~
        classification_hint: bug_fix      # operation | bug_fix | demand | internal_task
        notes: "示例条目"
        is_active: true                   # default true

Usage:
    cd backend
    .venv/bin/python ../scripts/seed/seed_ksm_type_mappings.py \\
        config/mappings/ksm_issue_types.yaml --dsn $PG_DSN [--prune] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import KSMIssueTypeMapping  # noqa: E402

_VALID_HINTS = {"operation", "bug_fix", "demand", "internal_task", None}


@dataclass(slots=True)
class SeedReport:
    rows_added: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0  # already in target state
    rows_pruned: int = 0  # only when --prune
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = ["seed_ksm_type_mappings report:"]
        for k in ("rows_added", "rows_updated", "rows_skipped", "rows_pruned"):
            lines.append(f"  {k}: {getattr(self, k)}")
        if self.warnings:
            lines.append(f"  warnings ({len(self.warnings)}):")
            for w in self.warnings[:20]:
                lines.append(f"    - {w}")
        return "\n".join(lines)


def seed_from_yaml(
    db: Session, spec: dict[str, Any], *, prune: bool = False
) -> SeedReport:
    """Apply a parsed yaml spec. Caller commits."""
    rep = SeedReport()
    items = spec.get("mappings") or []
    if not isinstance(items, list):
        rep.warnings.append("'mappings' must be a list")
        return rep

    yaml_keys: set[tuple[str, str | None]] = set()

    for raw in items:
        if not isinstance(raw, dict):
            rep.warnings.append(f"non-mapping yaml item skipped: {raw!r}")
            continue
        category = raw.get("ksm_category")
        subcategory = raw.get("ksm_subcategory")  # may be None
        if not isinstance(category, str) or not category.strip():
            rep.warnings.append(f"missing ksm_category: {raw!r}")
            continue
        product_line = raw.get("product_line_code")
        target_module = raw.get("target_module")
        if not (isinstance(product_line, str) and isinstance(target_module, str)):
            rep.warnings.append(
                f"product_line_code + target_module required: {category}/{subcategory}"
            )
            continue
        hint = raw.get("classification_hint")
        if hint not in _VALID_HINTS:
            rep.warnings.append(
                f"invalid classification_hint={hint!r} for {category}/{subcategory}"
            )
            continue

        yaml_keys.add((category, subcategory))

        # Upsert by UNIQUE (ksm_category, ksm_subcategory)
        existing = db.execute(
            select(KSMIssueTypeMapping).where(
                KSMIssueTypeMapping.ksm_category == category,
                KSMIssueTypeMapping.ksm_subcategory == subcategory,
            )
        ).scalar_one_or_none()

        target_feature = raw.get("target_feature")
        notes = raw.get("notes")
        is_active = bool(raw.get("is_active", True))

        if existing is None:
            db.add(
                KSMIssueTypeMapping(
                    ksm_category=category,
                    ksm_subcategory=subcategory,
                    product_line_code=product_line,
                    target_module=target_module,
                    target_feature=target_feature,
                    classification_hint=hint,
                    notes=notes,
                    is_active=is_active,
                )
            )
            rep.rows_added += 1
        else:
            changed = False
            if existing.product_line_code != product_line:
                existing.product_line_code = product_line
                changed = True
            if existing.target_module != target_module:
                existing.target_module = target_module
                changed = True
            if existing.target_feature != target_feature:
                existing.target_feature = target_feature
                changed = True
            if existing.classification_hint != hint:
                existing.classification_hint = hint
                changed = True
            if existing.notes != notes:
                existing.notes = notes
                changed = True
            if existing.is_active != is_active:
                existing.is_active = is_active
                changed = True
            if changed:
                rep.rows_updated += 1
            else:
                rep.rows_skipped += 1

    db.flush()

    # Prune: rows in DB but absent from yaml. Off by default to avoid wiping
    # manually-curated entries when seed file is stale.
    if prune:
        all_db_rows = list(db.execute(select(KSMIssueTypeMapping)).scalars().all())
        for row in all_db_rows:
            key = (row.ksm_category, row.ksm_subcategory)
            if key not in yaml_keys:
                db.delete(row)
                rep.rows_pruned += 1
        db.flush()

    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed ksm_issue_type_mappings from yaml")
    parser.add_argument("yaml_path", type=Path)
    parser.add_argument("--dsn", default=None, help="PG DSN (defaults to PG_DSN env)")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete DB rows whose (category, subcategory) is absent from yaml",
    )
    parser.add_argument("--dry-run", action="store_true", help="Roll back at the end")
    args = parser.parse_args()

    if not args.yaml_path.exists():
        print(f"yaml not found: {args.yaml_path}", file=sys.stderr)
        return 2

    spec = yaml.safe_load(args.yaml_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        print("yaml root must be a mapping", file=sys.stderr)
        return 2

    dsn = args.dsn
    if dsn is None:
        from app.config import get_settings

        dsn = get_settings().pg_dsn

    engine = create_engine(dsn, future=True)
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        report = seed_from_yaml(db, spec, prune=args.prune)
        if args.dry_run:
            db.rollback()
            print("(dry run — rolled back)")
        else:
            db.commit()
        print(report.render())
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
