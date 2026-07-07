"""owner-split API 测试（ADR-0016 P4）：POST /owner-split + detail.sub_issues."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, HubIssueLinearIssue, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=90,
            short_code="HUB-000090",
            type="Demand",
            title="批量导出",
            status="in_progress",
            linear_uuid="parent-uuid",
            linear_identifier="CNPRD-90",
        )
    )
    db_session.commit()
    return db_session


def test_owner_split_endpoint(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.hub_issues import owner_split as os_svc

    def fake_execute(db, hub_issue_id, *, subtasks, executed_by, client=None):  # type: ignore[no-untyped-def]
        assert hub_issue_id == 90
        assert executed_by == "user:carol"
        assert [s.title for s in subtasks] == ["接口", "前端"]
        return os_svc.OwnerSplitResult(
            hub_issue_id=90,
            sub_issues=[
                os_svc.SubIssueOut(
                    id=1,
                    linear_uuid="u1",
                    linear_identifier="CNPRD-91",
                    title="接口",
                    assignee_user_id=2,
                ),
                os_svc.SubIssueOut(
                    id=2,
                    linear_uuid="u2",
                    linear_identifier="CNPRD-92",
                    title="前端",
                    assignee_user_id=None,
                ),
            ],
        )

    monkeypatch.setattr(os_svc, "execute_owner_split", fake_execute)
    resp = app_client.post(
        "/api/hub-issues/90/owner-split",
        json={
            "subtasks": [
                {"title": "接口", "assignee_user_id": 2},
                {"title": "前端"},
            ]
        },
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["linear_identifier"] for s in body["sub_issues"]] == ["CNPRD-91", "CNPRD-92"]


def test_owner_split_requires_supervisor(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/owner-split",
        json={"subtasks": [{"title": "a"}, {"title": "b"}]},
        headers=_bearer(1, name="bob", role="assignee"),
    )
    assert r.status_code == 403


def test_owner_split_min_two_subtasks_422(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/owner-split",
        json={"subtasks": [{"title": "only-one"}]},
        headers=_bearer(2),
    )
    assert r.status_code == 422


def test_owner_split_service_error_409(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.hub_issues import owner_split as os_svc

    def boom(*a, **kw):  # type: ignore[no-untyped-def]
        raise os_svc.OwnerSplitError("已拆分过")

    monkeypatch.setattr(os_svc, "execute_owner_split", boom)
    r = app_client.post(
        "/api/hub-issues/90/owner-split",
        json={"subtasks": [{"title": "a"}, {"title": "b"}]},
        headers=_bearer(2),
    )
    assert r.status_code == 409
    assert "已拆分过" in r.json()["detail"]


def test_detail_includes_sub_issues(app_client: TestClient, world: Session) -> None:
    world.add(
        HubIssueLinearIssue(
            hub_issue_id=90,
            linear_uuid="u1",
            linear_identifier="CNPRD-91",
            title="接口",
            status="In Progress",
            state_type="started",
            created_by="user:carol",
        )
    )
    world.commit()
    resp = app_client.get("/api/hub-issues/90", headers=_bearer(2))
    assert resp.status_code == 200
    subs = resp.json()["sub_issues"]
    assert len(subs) == 1
    assert subs[0]["linear_identifier"] == "CNPRD-91"
    assert subs[0]["status"] == "In Progress"
    assert subs[0]["released_at"] is None
