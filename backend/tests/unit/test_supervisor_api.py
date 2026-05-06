"""Tests for /api/supervisor/* endpoints + JWT auth dependency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.config import get_settings
from app.models import (
    HubIssue,
    NotificationLog,
    Source,
    Ticket,
    TicketHubIssueHistory,
    User,
)

# ---- helpers ---------------------------------------------------------------


def _bearer(user_id: int, *, name: str = "test", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def supervisor_world(db_session: Session) -> tuple[Session, dict[str, User]]:
    db_session.add(Source(code="ksm", name="KSM"))
    users = {
        "alice": User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
        "carol": User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"),
        "dave": User(id=3, feishu_uid="ou_dave", name="dave", role="admin"),
    }
    db_session.add_all(users.values())
    db_session.add_all(
        [
            HubIssue(id=10, short_code="HUB-A", type="Operation", title="A", status="created"),
            HubIssue(id=20, short_code="HUB-B", type="Operation", title="B", status="created"),
        ]
    )
    db_session.flush()
    db_session.add(
        Ticket(
            id=100,
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="linked",
            hub_issue_id=10,
        )
    )
    db_session.flush()
    db_session.add(TicketHubIssueHistory(ticket_id=100, hub_issue_id=10, change_reason="initial"))
    # Some pending notifications for carol
    db_session.add_all(
        [
            NotificationLog(
                recipient_user_id=2,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={"k": "v1"},
            ),
            NotificationLog(
                recipient_user_id=2,
                channel="feishu_bot",
                notify_type="escalation",
                payload={"k": "v2"},
            ),
            # already-acknowledged: should NOT show
            NotificationLog(
                recipient_user_id=2,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={"k": "v3"},
                acknowledged_at=datetime.now(UTC) - timedelta(hours=1),
            ),
            # for someone else: should NOT show
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={"k": "v4"},
            ),
        ]
    )
    db_session.commit()
    return db_session, users


# ---- JWT auth dependency --------------------------------------------------


def test_inbox_requires_jwt(app_client: TestClient) -> None:
    resp = app_client.get("/api/supervisor/inbox")
    assert resp.status_code == 401


def test_inbox_invalid_jwt_rejected(app_client: TestClient) -> None:
    resp = app_client.get("/api/supervisor/inbox", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


def test_inbox_assignee_role_forbidden(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.get("/api/supervisor/inbox", headers=_bearer(1, role="assignee"))
    assert resp.status_code == 403


def test_inbox_expired_token_rejected(app_client: TestClient) -> None:
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "2",
            "name": "carol",
            "role": "supervisor",
            "iat": int((datetime.now(UTC) - timedelta(days=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(days=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    resp = app_client.get("/api/supervisor/inbox", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


# ---- /inbox ---------------------------------------------------------------


def test_inbox_returns_only_pending_for_self(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.get("/api/supervisor/inbox", headers=_bearer(2))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2  # 2 pending for carol (acked + others'-recipient excluded)
    for item in items:
        assert item["payload"]["k"] in {"v1", "v2"}


def test_inbox_admin_can_view_own_inbox(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.get("/api/supervisor/inbox", headers=_bearer(3, role="admin"))
    assert resp.status_code == 200
    # dave has no notifications, but the endpoint accepts admins
    assert resp.json()["items"] == []


# ---- /notifications/{id}/ack ---------------------------------------------


def test_ack_marks_acknowledged(
    app_client: TestClient,
    supervisor_world: tuple[Session, dict],
    db_session: Session,
) -> None:
    db = supervisor_world[0]
    notif_ids = [
        n.id
        for n in db.query(NotificationLog)
        .filter(NotificationLog.recipient_user_id == 2)
        .filter(NotificationLog.acknowledged_at.is_(None))
        .all()
    ]
    target_id = notif_ids[0]
    resp = app_client.post(f"/api/supervisor/notifications/{target_id}/ack", headers=_bearer(2))
    assert resp.status_code == 200
    body = resp.json()
    assert body["notification_id"] == target_id

    # 404 on unknown id
    resp2 = app_client.post("/api/supervisor/notifications/9999/ack", headers=_bearer(2))
    assert resp2.status_code == 404


def test_ack_others_notification_forbidden(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    db = supervisor_world[0]
    # carol's notif
    target = (
        db.query(NotificationLog)
        .filter(NotificationLog.recipient_user_id == 2)
        .filter(NotificationLog.acknowledged_at.is_(None))
        .first()
    )
    assert target is not None
    # dave (admin) tries to ack carol's notif
    resp = app_client.post(
        f"/api/supervisor/notifications/{target.id}/ack",
        headers=_bearer(3, role="admin"),
    )
    assert resp.status_code == 403


def test_ack_idempotent(
    app_client: TestClient,
    supervisor_world: tuple[Session, dict],
) -> None:
    db = supervisor_world[0]
    target = (
        db.query(NotificationLog)
        .filter(NotificationLog.recipient_user_id == 2)
        .filter(NotificationLog.acknowledged_at.is_(None))
        .first()
    )
    assert target is not None
    r1 = app_client.post(f"/api/supervisor/notifications/{target.id}/ack", headers=_bearer(2))
    r2 = app_client.post(f"/api/supervisor/notifications/{target.id}/ack", headers=_bearer(2))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["acknowledged_at"] == r2.json()["acknowledged_at"]


# ---- /relink --------------------------------------------------------------


def test_relink_succeeds(app_client: TestClient, supervisor_world: tuple[Session, dict]) -> None:
    resp = app_client.post(
        "/api/supervisor/relink",
        json={
            "ticket_id": 100,
            "new_hub_issue_id": 20,
            "reason": "investigation found root cause is HUB-B",
        },
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["no_op"] is False
    assert body["old_hub_issue_id"] == 10
    assert body["new_hub_issue_id"] == 20


def test_relink_to_same_target_is_noop(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.post(
        "/api/supervisor/relink",
        json={"ticket_id": 100, "new_hub_issue_id": 10, "reason": "noop"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200
    assert resp.json()["no_op"] is True


def test_relink_unknown_ticket_returns_404(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.post(
        "/api/supervisor/relink",
        json={"ticket_id": 9999, "new_hub_issue_id": 20},
        headers=_bearer(2),
    )
    assert resp.status_code == 404


def test_relink_assignee_role_forbidden(
    app_client: TestClient, supervisor_world: tuple[Session, dict]
) -> None:
    resp = app_client.post(
        "/api/supervisor/relink",
        json={"ticket_id": 100, "new_hub_issue_id": 20},
        headers=_bearer(1, role="assignee"),
    )
    assert resp.status_code == 403
