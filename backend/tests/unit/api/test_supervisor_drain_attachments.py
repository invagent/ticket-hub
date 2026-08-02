"""POST /api/supervisor/drain-attachments endpoint test.

Auth gate + the disabled-default path through the real endpoint wiring
(attachment_pipeline_enabled defaults False, so drain returns all zeros —
no MinIO/KSM/vision calls happen)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Source, User


def _bearer(user_id: int, *, role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="carol", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(User(id=3, feishu_uid="ou_bob", name="bob", role="member"))
    db_session.commit()
    return db_session


def test_drain_attachments_requires_supervisor(app_client: TestClient, world: Session) -> None:
    resp = app_client.post("/api/supervisor/drain-attachments", headers=_bearer(3, role="member"))
    assert resp.status_code == 403


def test_drain_attachments_returns_report(app_client: TestClient, world: Session) -> None:
    resp = app_client.post("/api/supervisor/drain-attachments", headers=_bearer(2))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"scanned", "extracted", "skipped", "failed"}
    # attachment_pipeline_enabled defaults False → all zeros.
    assert body == {"scanned": 0, "extracted": 0, "skipped": 0, "failed": 0}
