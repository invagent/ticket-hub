"""Tests for scripts/seed/seed_ksm_type_mappings.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.seed.seed_ksm_type_mappings import seed_from_yaml  # noqa: E402

from app.models import KSMIssueTypeMapping, ProductLine  # noqa: E402


def _seed_pl(db: Session) -> None:
    """ksm_issue_type_mappings.product_line_code FK requires product_lines exist."""
    db.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db.add(ProductLine(code="hcm", name="HCM"))
    db.commit()


@pytest.fixture
def small_spec() -> dict:
    return {
        "mappings": [
            {
                "ksm_category": "财务-应付审核",
                "ksm_subcategory": None,
                "product_line_code": "cloud-erp",
                "target_module": "应付管理",
                "target_feature": None,
                "classification_hint": "bug_fix",
                "notes": "示例条目",
            },
            {
                "ksm_category": "财务-应收对账",
                "ksm_subcategory": None,
                "product_line_code": "cloud-erp",
                "target_module": "应收管理",
                "target_feature": None,
                "classification_hint": "operation",
            },
        ]
    }


# ---- first run -----------------------------------------------------------


def test_first_run_inserts_all_mappings(db_session: Session, small_spec: dict) -> None:
    _seed_pl(db_session)
    rep = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep.rows_added == 2
    assert rep.rows_updated == 0
    assert rep.rows_skipped == 0
    assert rep.warnings == []
    assert db_session.query(KSMIssueTypeMapping).count() == 2


# ---- idempotent ----------------------------------------------------------


def test_replay_is_idempotent(db_session: Session, small_spec: dict) -> None:
    _seed_pl(db_session)
    seed_from_yaml(db_session, small_spec)
    db_session.commit()

    rep2 = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep2.rows_added == 0
    assert rep2.rows_updated == 0
    assert rep2.rows_skipped == 2
    assert db_session.query(KSMIssueTypeMapping).count() == 2


# ---- update --------------------------------------------------------------


def test_changed_target_module_triggers_update(db_session: Session, small_spec: dict) -> None:
    _seed_pl(db_session)
    seed_from_yaml(db_session, small_spec)
    db_session.commit()

    # Operator decides 应付审核 should route to a different module
    small_spec["mappings"][0]["target_module"] = "应付审核-新版"
    rep = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep.rows_updated == 1
    assert rep.rows_skipped == 1  # the other row unchanged

    row = (
        db_session.query(KSMIssueTypeMapping)
        .filter(KSMIssueTypeMapping.ksm_category == "财务-应付审核")
        .one()
    )
    assert row.target_module == "应付审核-新版"


def test_subcategory_distinguishes_unique_rows(db_session: Session) -> None:
    """(ksm_category, ksm_subcategory) is the unique key. Same category +
    different subcategory should be two distinct rows.
    """
    _seed_pl(db_session)
    spec = {
        "mappings": [
            {
                "ksm_category": "财务",
                "ksm_subcategory": "应付审核",
                "product_line_code": "cloud-erp",
                "target_module": "应付管理",
                "classification_hint": "bug_fix",
            },
            {
                "ksm_category": "财务",
                "ksm_subcategory": "应收对账",
                "product_line_code": "cloud-erp",
                "target_module": "应收管理",
                "classification_hint": "operation",
            },
        ]
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.rows_added == 2
    assert db_session.query(KSMIssueTypeMapping).count() == 2


# ---- prune ---------------------------------------------------------------


def test_default_does_not_delete_out_of_yaml_rows(db_session: Session, small_spec: dict) -> None:
    """Safety: stale yaml must not wipe DB-only rows."""
    _seed_pl(db_session)
    seed_from_yaml(db_session, small_spec)
    db_session.commit()

    # Yaml shrinks to one row; DB still has the other one
    small_spec["mappings"].pop()
    rep = seed_from_yaml(db_session, small_spec)
    db_session.commit()
    assert rep.rows_pruned == 0
    assert db_session.query(KSMIssueTypeMapping).count() == 2  # both still there


def test_prune_deletes_out_of_yaml_rows(db_session: Session, small_spec: dict) -> None:
    """With --prune, rows absent from yaml are deleted."""
    _seed_pl(db_session)
    seed_from_yaml(db_session, small_spec)
    db_session.commit()

    small_spec["mappings"].pop()
    rep = seed_from_yaml(db_session, small_spec, prune=True)
    db_session.commit()
    assert rep.rows_pruned == 1
    assert db_session.query(KSMIssueTypeMapping).count() == 1


# ---- validation warnings -------------------------------------------------


def test_invalid_classification_hint_warned_and_skipped(
    db_session: Session,
) -> None:
    _seed_pl(db_session)
    spec = {
        "mappings": [
            {
                "ksm_category": "财务-应付审核",
                "ksm_subcategory": None,
                "product_line_code": "cloud-erp",
                "target_module": "应付管理",
                "classification_hint": "BOGUS",  # invalid
            }
        ]
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.rows_added == 0
    assert any("classification_hint" in w for w in rep.warnings)


def test_missing_required_fields_warned(db_session: Session) -> None:
    _seed_pl(db_session)
    spec = {
        "mappings": [
            {"ksm_category": "x"},  # missing product_line / target_module
            {"ksm_subcategory": "y"},  # missing ksm_category
        ]
    }
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.rows_added == 0
    assert len(rep.warnings) >= 2


# ---- example yaml integrity ----------------------------------------------


def test_example_yaml_loads_cleanly(db_session: Session) -> None:
    _seed_pl(db_session)
    yaml_path = REPO_ROOT / "backend" / "config" / "mappings" / "ksm_issue_types.yaml"
    assert yaml_path.exists()
    spec = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    rep = seed_from_yaml(db_session, spec)
    db_session.commit()
    assert rep.warnings == []
    assert rep.rows_added == 2  # the example has 2 mappings
