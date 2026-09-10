"""Tests for attachment upload endpoint and subtask attachments population."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Attachment, HubIssue, Ticket, User


def _bearer(uid: int = 1, *, name: str = "alice", role: str = "assignee") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def upload_world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_u1", name="alice", role="assignee"))
    t = Ticket(
        id=501,
        short_code="TKT-000501",
        source_code="ksm",
        source_ticket_id="ksm-501",
        type="Raw",
        status="processing",
        title="上传附件测试工单",
        assigned_user_id=1,
    )
    db_session.add(t)
    db_session.flush()

    h = HubIssue(
        id=601,
        ticket_id=501,
        short_code="HUB-000601",
        type="Bug_fix",
        title="测试子任务",
        status="draft",
        assigned_user_id=1,
    )
    db_session.add(h)
    db_session.commit()
    return db_session


def test_upload_attachment_success(app_client: TestClient, upload_world: Session) -> None:
    raw_bytes = b"fake-png-content"
    b64 = base64.b64encode(raw_bytes).decode("ascii")

    resp = app_client.post(
        "/api/tickets/501/attachments/upload",
        json={
            "filename": "HUB-000601-1.png",
            "content_base64": b64,
            "hub_issue_id": 601,
            "mime": "image/png",
        },
        headers=_bearer(1),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] > 0
    assert data["filename"] == "HUB-000601-1.png"
    assert data["kind"] == "image"
    assert data["size_bytes"] == len(raw_bytes)
    assert data["hub_issue_id"] == 601
    assert data["download_url"] == f"/api/tickets/501/attachments/{data['id']}/download"

    # DB persistence
    att = upload_world.get(Attachment, data["id"])
    assert att is not None
    assert att.ticket_id == 501
    assert att.hub_issue_id == 601
    assert att.filename == "HUB-000601-1.png"


def test_upload_attachment_ticket_not_found(app_client: TestClient, upload_world: Session) -> None:
    resp = app_client.post(
        "/api/tickets/99999/attachments/upload",
        json={"filename": "test.txt", "content_base64": "dGVzdA=="},
        headers=_bearer(1),
    )
    assert resp.status_code == 404
    assert "ticket not found" in resp.text


def test_upload_attachment_hub_not_found(app_client: TestClient, upload_world: Session) -> None:
    resp = app_client.post(
        "/api/tickets/501/attachments/upload",
        json={"filename": "test.txt", "content_base64": "dGVzdA==", "hub_issue_id": 99999},
        headers=_bearer(1),
    )
    assert resp.status_code == 404
    assert "hub_issue not found" in resp.text


def test_list_ticket_subtasks_includes_attachments(
    app_client: TestClient, upload_world: Session
) -> None:
    # Upload an attachment to hub 601 first
    app_client.post(
        "/api/tickets/501/attachments/upload",
        json={
            "filename": "HUB-000601-1.png",
            "content_base64": base64.b64encode(b"img").decode("ascii"),
            "hub_issue_id": 601,
        },
        headers=_bearer(1),
    )

    resp = app_client.get("/api/tickets/501/subtasks", headers=_bearer(1))
    assert resp.status_code == 200, resp.text
    subs = resp.json()
    assert len(subs) == 1
    stk = subs[0]
    assert stk["id"] == 601
    assert len(stk["attachments"]) == 1
    att = stk["attachments"][0]
    assert att["filename"] == "HUB-000601-1.png"
    assert att["hub_issue_id"] == 601
    assert att["download_url"].startswith("/api/tickets/501/attachments/")
