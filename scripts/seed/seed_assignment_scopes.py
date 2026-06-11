"""Seed routing分工 (users + partners + product_lines + module/feature scopes) from yaml.

Idempotent: re-running with the same yaml is a no-op (no duplicate rows, no error).
Uses `employee_no` as the canonical user lookup key.

Yaml schema (see backend/config/seeds/assignment_scopes.example.yaml):

    product_lines:
      - {code: cloud-erp, name: Cloud ERP}
    users:
      - {employee_no: K0001, name: 张三, role: assignee}
    partners:
      - [K0001, K0002]                                 # symmetric backup pair
    module_scopes:
      - {employee_no: K0001, product_line: cloud-erp, module: 应付管理}
    feature_scopes:
      - {employee_no: K0001, feature: 数据导入}

Audit: every scope insert writes one assignment_scope_history row with
changed_by = the synthetic 'system:seed' admin user (auto-created on first run).

Usage:
    cd backend
    .venv/bin/python ../scripts/seed/seed_assignment_scopes.py \\
        ../backend/config/seeds/assignment_scopes.example.yaml \\
        --dsn $PG_DSN [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Make the backend `app` importable when running from repo root or backend/.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    AssignmentScopeFeature,
    AssignmentScopeModule,
    ProductLine,
    User,
    UserPartner,
)
from app.repositories.assignment_scope import AssignmentScopeAdminRepository  # noqa: E402

SYSTEM_SEED_FEISHU_UID = "system:seed"


@dataclass(slots=True)
class SeedReport:
    product_lines_added: int = 0
    users_added: int = 0
    users_updated: int = 0
    users_skipped: int = 0
    partners_added: int = 0
    partners_skipped: int = 0
    module_scopes_added: int = 0
    module_scopes_skipped: int = 0
    feature_scopes_added: int = 0
    feature_scopes_skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = ["seed_assignment_scopes report:"]
        for k in (
            "product_lines_added",
            "users_added",
            "users_updated",
            "users_skipped",
            "partners_added",
            "partners_skipped",
            "module_scopes_added",
            "module_scopes_skipped",
            "feature_scopes_added",
            "feature_scopes_skipped",
        ):
            lines.append(f"  {k}: {getattr(self, k)}")
        if self.warnings:
            lines.append(f"  warnings ({len(self.warnings)}):")
            for w in self.warnings[:20]:
                lines.append(f"    - {w}")
        return "\n".join(lines)


def _ensure_system_user(db: Session) -> User:
    """Auto-create the 'system:seed' admin attribution user on first run."""
    user = db.query(User).filter(User.feishu_uid == SYSTEM_SEED_FEISHU_UID).one_or_none()
    if user is None:
        user = User(
            feishu_uid=SYSTEM_SEED_FEISHU_UID,
            name="system:seed",
            role="admin",
            is_active=False,  # not a real human; not active in directory
        )
        db.add(user)
        db.flush()
    return user


def _seed_product_lines(db: Session, items: list[dict[str, Any]], rep: SeedReport) -> None:
    for item in items:
        code = item["code"]
        existing = db.query(ProductLine).filter(ProductLine.code == code).one_or_none()
        if existing is not None:
            continue
        db.add(ProductLine(code=code, name=item.get("name", code)))
        rep.product_lines_added += 1
    db.flush()


def _seed_users(db: Session, items: list[dict[str, Any]], rep: SeedReport) -> dict[str, User]:
    """Returns mapping employee_no → User for downstream use."""
    out: dict[str, User] = {}
    for item in items:
        emp = item.get("employee_no")
        if not emp:
            rep.warnings.append(f"user spec missing employee_no: {item}")
            rep.users_skipped += 1
            continue
        user = db.query(User).filter(User.employee_no == emp).one_or_none()
        if user is None:
            user = User(
                # Placeholder feishu_uid until first SSO login binds the real one
                feishu_uid=item.get("feishu_uid") or f"pending:{emp}",
                employee_no=emp,
                name=item["name"],
                role=item.get("role", "assignee"),
            )
            db.add(user)
            db.flush()
            rep.users_added += 1
        else:
            # Update mutable fields (name) but never role (admin may have tweaked it)
            updated = False
            if item.get("name") and user.name != item["name"]:
                user.name = item["name"]
                updated = True
            if updated:
                rep.users_updated += 1
        out[emp] = user
    return out


def _seed_partners(
    db: Session,
    items: list[list[str]],
    by_emp: dict[str, User],
    rep: SeedReport,
) -> None:
    """Each [emp_a, emp_b] becomes 2 rows: (a, b) and (b, a) for symmetric search."""
    for pair in items:
        if len(pair) != 2:
            rep.warnings.append(f"partner pair must have exactly 2 elements: {pair}")
            continue
        a_emp, b_emp = pair
        a, b = by_emp.get(a_emp), by_emp.get(b_emp)
        if a is None or b is None:
            rep.warnings.append(f"partner pair refers to unknown employee_no: {pair}")
            continue
        for x, y in ((a, b), (b, a)):
            existing = db.execute(
                select(UserPartner).where(
                    UserPartner.user_id == x.id,
                    UserPartner.partner_id == y.id,
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(UserPartner(user_id=x.id, partner_id=y.id))
                rep.partners_added += 1
            else:
                rep.partners_skipped += 1
    db.flush()


def _seed_module_scopes(
    db: Session,
    items: list[dict[str, Any]],
    by_emp: dict[str, User],
    repo: AssignmentScopeAdminRepository,
    system_user_id: int,
    rep: SeedReport,
) -> None:
    for item in items:
        emp = item.get("employee_no")
        user = by_emp.get(emp) if emp else None
        if user is None:
            rep.warnings.append(f"module_scope: unknown employee_no={emp}")
            continue
        existing = db.execute(
            select(AssignmentScopeModule).where(
                AssignmentScopeModule.user_id == user.id,
                AssignmentScopeModule.product_line_code == item["product_line"],
                AssignmentScopeModule.module == item["module"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            rep.module_scopes_skipped += 1
            continue
        repo.add_module(
            user_id=user.id,
            product_line_code=item["product_line"],
            module=item["module"],
            changed_by=system_user_id,
        )
        rep.module_scopes_added += 1


def _seed_feature_scopes(
    db: Session,
    items: list[dict[str, Any]],
    by_emp: dict[str, User],
    repo: AssignmentScopeAdminRepository,
    system_user_id: int,
    rep: SeedReport,
) -> None:
    for item in items:
        emp = item.get("employee_no")
        user = by_emp.get(emp) if emp else None
        if user is None:
            rep.warnings.append(f"feature_scope: unknown employee_no={emp}")
            continue
        existing = db.execute(
            select(AssignmentScopeFeature).where(
                AssignmentScopeFeature.user_id == user.id,
                AssignmentScopeFeature.feature == item["feature"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            rep.feature_scopes_skipped += 1
            continue
        repo.add_feature(
            user_id=user.id,
            feature=item["feature"],
            changed_by=system_user_id,
        )
        rep.feature_scopes_added += 1


def seed_from_yaml(db: Session, spec: dict[str, Any]) -> SeedReport:
    """Apply a parsed yaml spec. Caller commits the transaction."""
    rep = SeedReport()
    system_user = _ensure_system_user(db)
    repo = AssignmentScopeAdminRepository(db)

    _seed_product_lines(db, spec.get("product_lines") or [], rep)
    by_emp = _seed_users(db, spec.get("users") or [], rep)
    _seed_partners(db, spec.get("partners") or [], by_emp, rep)
    _seed_module_scopes(db, spec.get("module_scopes") or [], by_emp, repo, system_user.id, rep)
    _seed_feature_scopes(db, spec.get("feature_scopes") or [], by_emp, repo, system_user.id, rep)
    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed assignment scopes from yaml")
    parser.add_argument("yaml_path", type=Path)
    parser.add_argument("--dsn", default=None, help="PG DSN (defaults to PG_DSN env)")
    parser.add_argument("--dry-run", action="store_true", help="Roll back at the end")
    args = parser.parse_args()

    if not args.yaml_path.exists():
        print(f"yaml not found: {args.yaml_path}", file=sys.stderr)
        return 2

    spec = yaml.safe_load(args.yaml_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        print("yaml root must be a mapping", file=sys.stderr)
        return 2

    # Resolve DSN
    dsn = args.dsn
    if dsn is None:
        # Fall back to app settings (which reads PG_DSN env)
        from app.config import get_settings

        dsn = get_settings().pg_dsn

    engine = create_engine(dsn, future=True)
    session_factory = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()
    try:
        report = seed_from_yaml(db, spec)
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
