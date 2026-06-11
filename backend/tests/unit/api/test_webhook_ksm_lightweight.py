"""KSM webhook lightweight-ping mode (D2-F).

These tests exercise the production KSM contract:
  POST /webhook/ksm  with {billId, noticeNum, subscribeNum}
    → server stores latest pair in NoticeStore
    → server schedules BackgroundTask to call subscribeCallback + ingest
    → server returns {"code": 0} immediately

We mock the KSMClient HTTP calls via respx and replace the module-global
NoticeStore with the in-memory `FakeNoticeStore`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.api import webhooks as webhook_module
from app.models import (
    AssignmentScopeModule,
    ProductLine,
    Source,
    Ticket,
    User,
)
from app.services.ksm.notice_store import FakeNoticeStore, NoticeInfo

KSM_BASE = "https://ierp.kingdee.com"  # default config in test env


@pytest.fixture(autouse=True)
def _swap_notice_store(monkeypatch: pytest.MonkeyPatch) -> FakeNoticeStore:
    fake = FakeNoticeStore()
    monkeypatch.setattr(webhook_module, "_notice_store", fake)
    return fake


@pytest.fixture(autouse=True)
def _ksm_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KSM_BASE_URL", KSM_BASE)
    monkeypatch.setenv("KSM_APP_ID", "test-app")
    monkeypatch.setenv("KSM_APP_SECRET", "test-secret")
    monkeypatch.setenv("KSM_TENANT_ID", "kd_pro")
    monkeypatch.setenv("KSM_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("KSM_USER", "fapiaoyun")
    from app.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _swap_make_session(monkeypatch: pytest.MonkeyPatch, sqlite_engine) -> None:  # type: ignore[no-untyped-def]
    """Redirect BackgroundTask's make_session() to the same in-memory sqlite
    engine the test fixtures use, so the async ingest finds seeded data."""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(sqlite_engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(webhook_module, "make_session", SessionLocal)


@pytest.fixture
def ingest_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-erp-star", name="金蝶云星空"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-erp-star", module="财务模块")
    )
    db_session.commit()
    return db_session


def _stub_ksm_auth(rsps: respx.MockRouter) -> None:
    rsps.post(f"{KSM_BASE}/ierp/api/getAppToken.do").mock(
        return_value=httpx.Response(200, json={"data": {"app_token": "AT-x"}})
    )
    rsps.post(f"{KSM_BASE}/ierp/api/login.do").mock(
        return_value=httpx.Response(
            200, json={"data": {"access_token": "AC-x", "expire_time": 9999999999000}}
        )
    )


def _stub_subscribe_callback(rsps: respx.MockRouter, data: dict[str, Any]) -> None:
    rsps.post(f"{KSM_BASE}/ierp/kapi/app/open/subscribeCallback").mock(
        return_value=httpx.Response(200, json={"status": True, "data": data})
    )


# ---- core ping behavior ---------------------------------------------------


@respx.mock
def test_lightweight_ping_returns_code0_immediately(
    app_client, ingest_world: Session, _swap_notice_store: FakeNoticeStore
) -> None:
    """The webhook returns {"code": 0} BEFORE any HTTP call to KSM happens
    (i.e. respx will record subscribeCallback as a hit only if the
    BackgroundTask runs — TestClient runs background tasks synchronously
    after returning the response, but we assert the immediate response shape).
    """
    _stub_ksm_auth(respx)
    _stub_subscribe_callback(
        respx,
        {
            "billId": "BILL-LP-1",
            "title": "测试工单",
            "problem": "请求开票失败",
            "version": {"mainproductname": "金蝶云星空"},
            "module": {"name": "财务模块"},
            "customerInfo": {
                "customerNumber": "C-001",
                "customerName": "测试公司",
                "linkman": "陈某",
                "email": "chen@example.com",
                "mobile": "13900000000",
            },
        },
    )
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={
            "billId": "BILL-LP-1",
            "noticeNum": "N-100",
            "subscribeNum": "S-100",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}

    # NoticeStore was populated with the latest pair
    n = _swap_notice_store.get("BILL-LP-1")
    assert n == NoticeInfo(notice_num="N-100", subscribe_num="S-100")

    # BackgroundTask ran and ingested via KSMIngester
    t = ingest_world.query(Ticket).filter_by(source_ticket_id="BILL-LP-1").one()
    assert t.title == "测试工单"
    assert t.product_line_code == "cloud-erp-star"
    assert t.module == "财务模块"
    assert t.assigned_user_id == 1


@respx.mock
def test_lightweight_ping_id_field_fallback(app_client, ingest_world: Session) -> None:
    """Per doc: KSM sometimes uses `id` instead of `billId`."""
    _stub_ksm_auth(respx)
    _stub_subscribe_callback(
        respx,
        {
            "billId": "BILL-FALLBACK",
            "title": "id-fallback",
            "version": {"mainproductname": "金蝶云星空"},
            "module": {"name": "财务模块"},
            "customerInfo": {"customerNumber": "X", "customerName": "y"},
        },
    )
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={"id": "BILL-FALLBACK", "noticeNum": "N", "subscribeNum": "S"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}
    assert ingest_world.query(Ticket).filter_by(source_ticket_id="BILL-FALLBACK").count() == 1


@respx.mock
def test_lightweight_ping_missing_one_field_silently_acks(
    app_client, _swap_notice_store: FakeNoticeStore
) -> None:
    """Per doc § 二 #1: 三个字段均不为空，否则忽略."""
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={"billId": "X", "noticeNum": "N"},  # no subscribeNum
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}
    # Nothing stored
    assert _swap_notice_store.get("X") is None


@respx.mock
def test_lightweight_ping_overwrites_notice_store_on_re_push(
    app_client, ingest_world: Session, _swap_notice_store: FakeNoticeStore
) -> None:
    """Multiple pushes for same billId → store always reflects latest pair."""
    _stub_ksm_auth(respx)
    _stub_subscribe_callback(
        respx,
        {
            "billId": "BILL-RP",
            "title": "rp",
            "version": {"mainproductname": "金蝶云星空"},
            "module": {"name": "财务模块"},
            "customerInfo": {"customerNumber": "C", "customerName": "n"},
        },
    )
    for i in range(3):
        app_client.post(
            "/webhook/ksm?access_token=test-token",
            json={
                "billId": "BILL-RP",
                "noticeNum": f"N-{i}",
                "subscribeNum": f"S-{i}",
            },
        )
    # Latest pair retained
    assert _swap_notice_store.get("BILL-RP") == NoticeInfo("N-2", "S-2")
    # Only ONE ticket created (ingester dedup)
    assert ingest_world.query(Ticket).filter_by(source_ticket_id="BILL-RP").count() == 1


@respx.mock
def test_subscribe_callback_status_false_does_not_crash(app_client, ingest_world: Session) -> None:
    """KSM API success but business-failed (status=false) → BG task logs +
    swallows; webhook still returns code:0, no ticket created."""
    _stub_ksm_auth(respx)
    respx.post(f"{KSM_BASE}/ierp/kapi/app/open/subscribeCallback").mock(
        return_value=httpx.Response(200, json={"status": False, "message": "no such bill"})
    )
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={"billId": "BILL-NOTFOUND", "noticeNum": "N", "subscribeNum": "S"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}
    # No ticket created
    assert ingest_world.query(Ticket).filter_by(source_ticket_id="BILL-NOTFOUND").count() == 0


@respx.mock
def test_invalid_token_still_401_for_ping(app_client) -> None:
    resp = app_client.post(
        "/webhook/ksm?access_token=wrong",
        json={"billId": "x", "noticeNum": "n", "subscribeNum": "s"},
    )
    assert resp.status_code == 401
