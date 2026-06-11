"""ksm_payload mapper tests — D2-F.

Covers product-line resolution (exact match → prefix → None), customer
field extraction (top-level feedback* fields + customerInfo.customerNumber,
mapping fixed 2026-05-14), and that source_payload preserves the raw
subscribeCallback data for audit.
"""

from __future__ import annotations

import pytest

from app.services.ingest.ksm_payload import (
    PRODUCT_NAME_TO_CODE,
    from_subscribe_callback,
)

# ---- product line resolution -----------------------------------------------


@pytest.mark.parametrize(
    "name,expected_code",
    [
        ("金蝶发票云", "cloud-fapiao"),
        ("金蝶云星空", "cloud-erp-star"),
        ("金蝶云苍穹", "cloud-cangqiong"),
        ("金蝶EAS Cloud", "eas-cloud"),
        ("金蝶 EAS Cloud", "eas-cloud"),
    ],
)
def test_exact_match(name: str, expected_code: str) -> None:
    data = {"version": {"mainproductname": name}}
    out = from_subscribe_callback(data)
    assert out["productLineCode"] == expected_code


@pytest.mark.parametrize(
    "name,expected_code",
    [
        # Real KSM data we saw in production
        ("金蝶发票云（旗舰版）私有云（订阅）", "cloud-fapiao"),
        ("金蝶发票云（旗舰版）", "cloud-fapiao"),
        ("金蝶云星空 V8.x", "cloud-erp-star"),
        ("金蝶云苍穹 SaaS", "cloud-cangqiong"),
    ],
)
def test_prefix_match(name: str, expected_code: str) -> None:
    """KSM trails product names with edition/deployment parens; prefix
    match against our base mapping handles all variants."""
    data = {"version": {"mainproductname": name}}
    out = from_subscribe_callback(data)
    assert out["productLineCode"] == expected_code


def test_longest_prefix_wins() -> None:
    """When two prefixes both match (e.g. 金蝶EAS vs 金蝶EAS Cloud),
    the longer one wins so we don't down-rank specific products."""
    # Add a colliding test entry
    PRODUCT_NAME_TO_CODE["金蝶EAS"] = "eas-cloud"  # already there but explicit
    data = {"version": {"mainproductname": "金蝶EAS Cloud V8"}}
    out = from_subscribe_callback(data)
    # Both "金蝶EAS" and "金蝶EAS Cloud" prefixes match; longer one wins
    assert out["productLineCode"] == "eas-cloud"


def test_unknown_product_returns_none() -> None:
    """Unmapped name MUST become None (not the raw string) — otherwise
    the Ticket FK to product_lines.code would violate."""
    data = {"version": {"mainproductname": "一个我们没见过的产品"}}
    out = from_subscribe_callback(data)
    assert out["productLineCode"] is None


def test_empty_inputs() -> None:
    assert from_subscribe_callback({})["productLineCode"] is None
    assert from_subscribe_callback({"version": {}})["productLineCode"] is None
    assert from_subscribe_callback({"version": {"mainproductname": ""}})["productLineCode"] is None


def test_falls_back_to_product_name_when_version_missing() -> None:
    data = {"product": {"name": "金蝶发票云"}}
    assert from_subscribe_callback(data)["productLineCode"] == "cloud-fapiao"


# ---- field extraction ------------------------------------------------------


def test_full_field_mapping_from_doc_example() -> None:
    """Real subscribeCallback shape: contact fields live at the TOP level
    (feedbackUser/feedbackEmail/feedbackPhone/feedbackTel), only the
    customer number comes from customerInfo (mapping fixed 2026-05-14)."""
    data = {
        "billId": "R20240101-0001",
        "title": "工单主题",
        "problem": "问题描述内容",
        "version": {"mainproductname": "金蝶云星空"},
        "module": {"name": "财务模块"},
        "feedbackUser": "李四",
        "feedbackEmail": "lisi@example.com",
        "feedbackPhone": "13900139000",
        "feedbackTel": "010-87654321",
        "customerInfo": {
            "customerName": "某某公司",
            "customerNumber": "C001",
        },
    }
    out = from_subscribe_callback(data)
    assert out["billId"] == "R20240101-0001"
    assert out["title"] == "工单主题"
    assert out["content"] == "问题描述内容"
    assert out["productLineCode"] == "cloud-erp-star"
    assert out["moduleName"] == "财务模块"
    assert out["account"] == "C001"
    assert out["accountName"] == "李四"
    assert out["email"] == "lisi@example.com"
    assert out["mobile"] == "13900139000"
    assert out["tel"] == "010-87654321"
    assert out["erpUid"] == "C001"
    # source_payload preserved for audit
    assert out["_subscribe_callback"] is data


def test_customerinfo_contact_fields_ignored() -> None:
    """customerInfo.linkman/mobile/email must NOT bleed into the contact
    fields — those belong to the account, not the person who filed the
    ticket (the 2026-05-14 mapping fix)."""
    data = {
        "customerInfo": {
            "customerName": "某某公司",
            "customerNumber": "C001",
            "linkman": "李四",
            "mobile": "13900139000",
            "email": "lisi@example.com",
        }
    }
    out = from_subscribe_callback(data)
    assert out["account"] == "C001"
    assert out["accountName"] is None
    assert out["email"] is None
    assert out["mobile"] is None


def test_feedback_tel_mapped_independently_of_phone() -> None:
    data = {"feedbackTel": "010-12345678", "customerInfo": {"customerNumber": "C"}}
    out = from_subscribe_callback(data)
    assert out["tel"] == "010-12345678"
    assert out["mobile"] is None


def test_id_field_fallback_when_billid_missing() -> None:
    """KSM doc says it sometimes uses `id` instead of `billId`."""
    data = {"id": "ALT-1", "title": "alt"}
    assert from_subscribe_callback(data)["billId"] == "ALT-1"
