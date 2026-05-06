"""Tests for scripts/seed/seed_assignment_scopes.py.

Imports the script's helper directly to avoid invoking argparse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

# Make the script importable as a module
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.seed.seed_assignment_scopes import (  # noqa: E402
    SYSTEM_SEED_FEISHU_UID,
    seed_from_yaml,
)

from app.models import (  # noqa: E402  (after sys.path.insert)
    AssignmentScopeFeature,
    AssignmentScopeHistory,
    AssignmentScopeModule,
    ProductLine,
    User,
    UserPartner,
)

# ---- minimal fixture -----------------------------------------------------


@pytest.fixture
def small_spec() -> dict:
    return {
        "product_lines": [
            {"code": "cloud-erp", "name": "Cloud ERP"},
        ],
        "users": [
            {"employee_no": "K0001", "name": "alice", "role": "assignee"},
            {"employee_no": "K0002", "name": "bob", "role": "assignee"},
        ],
        "partners": [["K0001", "K0002"]],
        "module_scopes": [
            {"employee_no": "K0001", "product_line": "cloud-erp", "module": "应付管理"},
            {"employee_no": "K0002", "product_line": "cloud-erp", "module": "应付管理"},
        ],
        "feature_scopes": [
            {"employee_no": "K0001", "feature": "数据导入"},
        ],
    }


# ---- system user --------------------------------------------------------


def test_seed_creates_system_user(db_session: Session, small_spec: dict) -> None:
    seed_from_yaml(db_session, small_spec)
    db_session.commit()
    sys_user = db_session.query(User).filter(User.feishu_uid == SYSTEM_SEED_FEISHU_UID).one()
    assert sys_user.role == "admin"
    assert sys_user.is_active is False


def test_history_attributed_to_system_user(db_session: Session, small_spec: dict) -> None:
    seed_from_yaml(db_session, small_spec)
    db_session.commit()
    sys_user = db_session.query(User).filter(User.feishu_uid == SYSTEM_SEED_FEISHU_UID).one()
    histories = db_session.query(AssignmentScopeHistory).all()
    # 2 module + 1 feature = 3 history rows on first run
    assert len(histories) == 3
    assert all(h.changed_by == sys_user.id for h in histories)
    assert all(h.action == "add" for h in histories)


# ---- core counts --------------------------------------------------------


def test_first_run_inserts_everything(db_session: Session, small_spec: dict) -> None:
    rep = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep.product_lines_added == 1
    assert rep.users_added == 2
    assert rep.partners_added == 2  # symmetric pair → 2 rows
    assert rep.module_scopes_added == 2
    assert rep.feature_scopes_added == 1
    assert rep.warnings == []

    assert db_session.query(ProductLine).count() == 1
    # 2 employees + 1 system_user
    assert db_session.query(User).count() == 3
    assert db_session.query(UserPartner).count() == 2
    assert db_session.query(AssignmentScopeModule).count() == 2
    assert db_session.query(AssignmentScopeFeature).count() == 1


# ---- idempotency --------------------------------------------------------


def test_replay_is_idempotent(db_session: Session, small_spec: dict) -> None:
    seed_from_yaml(db_session, small_spec)
    db_session.commit()
    rep2 = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    # Second run inserts nothing
    assert rep2.product_lines_added == 0
    assert rep2.users_added == 0
    assert rep2.partners_added == 0
    assert rep2.partners_skipped == 2
    assert rep2.module_scopes_added == 0
    assert rep2.module_scopes_skipped == 2
    assert rep2.feature_scopes_added == 0
    assert rep2.feature_scopes_skipped == 1
    # Counts unchanged
    assert db_session.query(AssignmentScopeModule).count() == 2
    assert db_session.query(AssignmentScopeFeature).count() == 1
    # History rows NOT duplicated (no add → no audit row)
    assert db_session.query(AssignmentScopeHistory).count() == 3


# ---- updates ------------------------------------------------------------


def test_user_name_change_updated(db_session: Session, small_spec: dict) -> None:
    seed_from_yaml(db_session, small_spec)
    db_session.commit()
    # Change alice's name
    small_spec["users"][0]["name"] = "alice (updated)"
    rep = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep.users_updated == 1
    user = db_session.query(User).filter(User.employee_no == "K0001").one()
    assert user.name == "alice (updated)"


def test_user_role_never_overwritten_after_admin_promotes(
    db_session: Session, small_spec: dict
) -> None:
    """Admin promotes alice to supervisor via /admin/users; subsequent seed
    must NOT downgrade her back to assignee.
    """
    seed_from_yaml(db_session, small_spec)
    db_session.commit()
    alice = db_session.query(User).filter(User.employee_no == "K0001").one()
    alice.role = "supervisor"
    db_session.commit()

    seed_from_yaml(db_session, small_spec)  # spec still says role=assignee
    db_session.commit()

    db_session.refresh(alice)
    assert alice.role == "supervisor"  # preserved


# ---- warnings -----------------------------------------------------------


def test_unknown_employee_in_scope_emits_warning(db_session: Session) -> None:
    spec = {
        "product_lines": [{"code": "cloud-erp", "name": "X"}],
        "users": [{"employee_no": "K0001", "name": "alice"}],
        "module_scopes": [
            {"employee_no": "K9999", "product_line": "cloud-erp", "module": "x"},
        ],
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.module_scopes_added == 0
    assert any("K9999" in w for w in rep.warnings)


def test_user_missing_employee_no_skipped(db_session: Session) -> None:
    spec = {
        "users": [
            {"name": "ghost"},  # missing employee_no
            {"employee_no": "K0001", "name": "alice"},
        ],
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.users_skipped == 1
    assert rep.users_added == 1
    assert any("missing employee_no" in w for w in rep.warnings)


def test_malformed_partner_pair_warned(db_session: Session) -> None:
    spec = {
        "users": [
            {"employee_no": "K0001", "name": "a"},
            {"employee_no": "K0002", "name": "b"},
        ],
        "partners": [["K0001"]],  # only 1 element — invalid
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.partners_added == 0
    assert any("exactly 2" in w for w in rep.warnings)


# ---- example yaml integrity ---------------------------------------------


def test_example_yaml_loads_and_seeds_cleanly(db_session: Session) -> None:
    """The shipped example yaml must load + apply without warnings."""
    yaml_path = REPO_ROOT / "backend" / "config" / "seeds" / "assignment_scopes.example.yaml"
    assert yaml_path.exists()
    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()

    assert rep.warnings == []
    # Sanity: counts roughly match routing_v1.jsonl expectations
    assert rep.product_lines_added == 4
    assert rep.users_added == 21  # 20 assignees + 1 supervisor pool
    assert rep.module_scopes_added == 15
    assert rep.feature_scopes_added == 5
    assert rep.partners_added == 4  # 2 symmetric pairs × 2 directions
