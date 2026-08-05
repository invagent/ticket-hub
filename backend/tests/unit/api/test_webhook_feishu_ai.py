"""POST /webhook/feishu_ai — auth + ingest + 挂标准 triage 链（非 escalation）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Attachment, Source, Ticket


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="feishu_ai", name="飞书AI"))
    db_session.commit()
    return db_session


_BODY = {
    "session_id": "fa-web-1",
    "original_question": "开票点了没反应",
    "ai_answer": "确认认证后操作",
    "dissatisfaction": "做了没用",
    "customer": {"erp_uid": "ERP1"},
    "attachments": [{"url": "https://x/a.png"}],
}


def test_bad_token_401(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/webhook/feishu_ai?access_token=wrong", json=_BODY)
    assert r.status_code == 401


def test_feishu_ai_webhook_creates_ticket_and_schedules_triage(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.webhooks as wh

    called: list[int] = []
    monkeypatch.setattr(wh, "run_post_ingest_agents", lambda tid: called.append(tid))
    # escalation 链绝不能被挂上
    monkeypatch.setattr(
        wh, "run_escalation_agents", lambda tid: pytest.fail("must not run escalation chain")
    )

    r = app_client.post("/webhook/feishu_ai?access_token=test-token", json=_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deduped"] is False
    t = world.get(Ticket, body["ticket_id"])
    assert t is not None and t.source_code == "feishu_ai"
    assert world.query(Attachment).filter_by(ticket_id=t.id).count() == 1
    assert called == [body["ticket_id"]]  # 挂的是标准 triage 链


def test_feishu_ai_webhook_missing_field_400(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/webhook/feishu_ai?access_token=test-token",
        json={"session_id": "s"},  # no original_question
    )
    assert r.status_code == 400
    assert "original_question" in r.json()["detail"]
