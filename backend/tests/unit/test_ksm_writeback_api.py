"""POST /api/supervisor/drain-ksm-writeback endpoint test (D4 第②段).

Auth gate + the enabled/dry-run path through the real endpoint wiring
(dry-run assembles but never calls KSM, so no network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.config import get_settings
from app.models import HubIssue, Source, SyncOutbox, Ticket, User


def _bearer(user_id: int, *, role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="carol", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.commit()
    return db_session


def test_requires_supervisor(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/api/supervisor/drain-ksm-writeback", headers=_bearer(3, role="member"))
    assert r.status_code == 403


def test_disabled_default_returns_empty(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/api/supervisor/drain-ksm-writeback", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["scanned"] == 0


def test_enabled_dry_run_skips_pending_row(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = HubIssue(id=80, short_code="HUB-000080", type="Operation", title="回写", status="created")
    world.add(hub)
    world.flush()
    t = Ticket(
        id=300,
        short_code="TKT-000300",
        source_code="ksm",
        source_ticket_id="BILL-9",
        type="Raw",
        status="received",
        title="工单",
        hub_issue_id=80,
        source_payload={"billId": "BILL-9", "_subscribe_callback": {"billId": "BILL-9"}},
    )
    world.add(t)
    world.add(
        SyncOutbox(
            kind="reply",
            target_source_code="ksm",
            ticket_id=300,
            source_ticket_id="BILL-9",
            hub_issue_id=80,
            payload={"reply_content": "已处理"},
        )
    )
    world.commit()

    monkeypatch.setenv("KSM_WRITEBACK_ENABLED", "true")
    monkeypatch.setenv("KSM_WRITEBACK_DRY_RUN", "true")
    monkeypatch.setenv("KSM_HANDLER_NAME", "李志坚")
    monkeypatch.setenv("KSM_HANDLER_NUMBER", "10086")
    get_settings.cache_clear()
    try:
        r = app_client.post("/api/supervisor/drain-ksm-writeback", headers=_bearer(2))
    finally:
        get_settings.cache_clear()

    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["dry_run"] is True
    assert body["scanned"] == 1 and body["skipped"] == 1 and body["sent"] == 0

    row = world.query(SyncOutbox).filter_by(ticket_id=300).one()
    assert row.status == "skipped"
