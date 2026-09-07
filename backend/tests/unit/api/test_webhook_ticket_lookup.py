"""GET /webhook/tickets/lookup — 第三方按工单号查询工单详情。

鉴权复用 webhook 同一个 access_token（settings.webhook_access_token），
ticket_no 精确匹配 source_ticket_number / source_ticket_id / short_code
三者之一。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Source, Ticket


def _ticket(db: Session, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-000001",
        "source_code": "ksm",
        "source_ticket_id": "BILL-001",
        "source_ticket_number": "R20260101-0001",
        "type": "Raw",
        "status": "received",
        "title": "工单标题",
        "body": "工单内容",
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_invalid_token_returns_401(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.get("/webhook/tickets/lookup?access_token=wrong&ticket_no=x")
    assert resp.status_code == 401


def test_lookup_by_source_ticket_number(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="ksm", name="KSM"))
    _ticket(db_session)
    resp = app_client.get(
        "/webhook/tickets/lookup?access_token=test-token&ticket_no=R20260101-0001"
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["short_code"] == "TKT-000001"
    assert items[0]["title"] == "工单标题"


def test_lookup_by_source_ticket_id(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="ksm", name="KSM"))
    _ticket(db_session)
    resp = app_client.get("/webhook/tickets/lookup?access_token=test-token&ticket_no=BILL-001")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["short_code"] == "TKT-000001"


def test_lookup_by_short_code(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="ksm", name="KSM"))
    _ticket(db_session)
    resp = app_client.get("/webhook/tickets/lookup?access_token=test-token&ticket_no=TKT-000001")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["source_ticket_id"] == "BILL-001"


def test_lookup_no_match_returns_empty_list_not_404(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="ksm", name="KSM"))
    _ticket(db_session)
    resp = app_client.get("/webhook/tickets/lookup?access_token=test-token&ticket_no=NOT-EXIST")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_lookup_excludes_soft_deleted(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    db_session.add(Source(code="ksm", name="KSM"))
    _ticket(db_session, deleted_at=datetime.now(UTC))
    resp = app_client.get(
        "/webhook/tickets/lookup?access_token=test-token&ticket_no=R20260101-0001"
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
