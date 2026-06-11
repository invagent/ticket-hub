"""Integrity gate for the D3 classify eval dataset.

Keeps dataset_v1.jsonl honest in CI without any LLM calls: every record
must parse, carry a valid expected_type, and ids must be unique. Also pins
the minimum size so the dataset can only grow (D3 target: 100 records).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "eval" / "dataset_v1.jsonl"
VALID_TYPES = {"Operation", "Bug_fix", "Demand", "Internal_task"}
REQUIRED_FIELDS = ("id", "origin", "title", "expected_type")
MIN_RECORDS = 60  # raise as the dataset grows; never lower


def _records() -> list[dict]:
    lines = DATASET.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_dataset_exists_and_min_size() -> None:
    records = _records()
    assert len(records) >= MIN_RECORDS, (
        f"dataset shrank to {len(records)} (< {MIN_RECORDS}) — eval baselines break"
    )


def test_every_record_well_formed() -> None:
    for rec in _records():
        for field in REQUIRED_FIELDS:
            assert rec.get(field), f"{rec.get('id', rec)}: missing {field!r}"
        assert rec["expected_type"] in VALID_TYPES, (
            f"{rec['id']}: bad expected_type {rec['expected_type']!r}"
        )


def test_ids_unique() -> None:
    ids = [r["id"] for r in _records()]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate ids: {dupes}"


def test_all_four_classes_present() -> None:
    """Per-class recall is meaningless if a class has zero support."""
    dist = Counter(r["expected_type"] for r in _records())
    missing = VALID_TYPES - set(dist)
    assert not missing, f"classes with no samples: {missing}"


def test_real_records_traceable_to_ksm_fixture() -> None:
    """Every ksm_historical record must reference a real recorded ticket."""
    fixture = (
        Path(__file__).resolve().parent.parent / "fixtures" / "recorded" / "historical_tickets.json"
    )
    known = {r["ksm_ticket_id"] for r in json.loads(fixture.read_text())["records"]}
    for rec in _records():
        if rec["origin"] == "ksm_historical":
            assert rec["source_ticket_id"] in known, (
                f"{rec['id']}: source_ticket_id not in historical_tickets.json"
            )
