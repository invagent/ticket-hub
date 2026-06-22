"""POST /webhook/cs-escalation — auth + ingest + BG chain scheduling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Attachment, Source, Ticket


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ai_cs", name="AI 客服"))
    db_session.commit()
    return db_session


_BODY = {
    "session_id": "sess-web-1",
    "original_question": "开票点了没反应",
    "ai_answer": "确认认证后操作",
    "dissatisfaction": "做了没用",
    "customer": {"erp_uid": "ERP1"},
    "attachments": [{"url": "https://x/a.png"}],
}


def test_bad_token_401(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/webhook/cs-escalation?access_token=wrong", json=_BODY)
    assert r.status_code == 401


def test_escalation_webhook_creates_ticket(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BG chain calls escalation_classify (LLM) — stub it out so no real call.
    import app.api.webhooks as wh

    monkeypatch.setattr(wh, "run_escalation_agents", lambda tid: None)

    r = app_client.post("/webhook/cs-escalation?access_token=test-token", json=_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deduped"] is False
    t = world.get(Ticket, body["ticket_id"])
    assert t is not None and t.source_code == "ai_cs"
    assert world.query(Attachment).filter_by(ticket_id=t.id).count() == 1


def test_escalation_webhook_missing_field_400(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/webhook/cs-escalation?access_token=test-token",
        json={"session_id": "s"},  # no original_question
    )
    assert r.status_code == 400
    assert "original_question" in r.json()["detail"]
