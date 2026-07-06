"""Admin /api/admin/skills/* API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import User


def _bearer(uid: int, *, name: str = "boss", role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="boss", role="admin"))
    db_session.commit()
    return db_session


def test_requires_admin(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/admin/skills", headers=_bearer(2, name="m", role="member"))
    assert r.status_code == 403


def test_import_then_list_get_edit_history_rollback(app_client: TestClient, world: Session) -> None:
    # import
    imp = app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    assert imp.status_code == 200 and imp.json()["added"] >= 5

    # list
    lst = app_client.get("/api/admin/skills", headers=_bearer(1)).json()
    names = {s["name"] for s in lst}
    assert "classify" in names and "dedup" in names

    # get
    detail = app_client.get("/api/admin/skills/dedup", headers=_bearer(1)).json()
    assert detail["version"] == 1 and detail["content_md"]

    # edit → v2
    e = app_client.put(
        "/api/admin/skills/dedup",
        json={"content_md": "新版去重提示词", "reason": "调优"},
        headers=_bearer(1),
    )
    assert e.status_code == 200 and e.json()["version"] == 2

    # history has v2, v1
    hist = app_client.get("/api/admin/skills/dedup/history", headers=_bearer(1)).json()
    assert [h["version"] for h in hist] == [2, 1]

    # rollback to v1 → v3
    rb = app_client.post(
        "/api/admin/skills/dedup/rollback", json={"version": 1}, headers=_bearer(1)
    )
    assert rb.status_code == 200 and rb.json()["version"] == 3


def test_get_missing_404(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.get("/api/admin/skills/nope_xyz", headers=_bearer(1))
    assert r.status_code == 404


def test_edit_missing_409(app_client: TestClient, world: Session) -> None:
    r = app_client.put("/api/admin/skills/nope_xyz", json={"content_md": "x"}, headers=_bearer(1))
    assert r.status_code == 409


def test_edit_empty_422(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.put("/api/admin/skills/dedup", json={"content_md": ""}, headers=_bearer(1))
    assert r.status_code == 422


# ---- 三槽 API（ADR-0016 P1）-------------------------------------------------


def test_draft_endpoints_roundtrip(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    # 存 draft → detail 可见三槽
    r = app_client.put(
        "/api/admin/skills/classify/draft",
        json={"content_md": "draft 内容"},
        headers=_bearer(1),
    )
    assert r.status_code == 200 and r.json()["has_draft"] is True
    detail = app_client.get("/api/admin/skills/classify", headers=_bearer(1)).json()
    assert detail["draft_md"] == "draft 内容"
    assert detail["previous_version"] is None

    # promote → current 换、draft 清、previous 出现
    r = app_client.post(
        "/api/admin/skills/classify/draft/promote",
        json={"reason": "验证通过"},
        headers=_bearer(1),
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    detail = app_client.get("/api/admin/skills/classify", headers=_bearer(1)).json()
    assert detail["content_md"] == "draft 内容"
    assert detail["draft_md"] is None
    assert detail["previous_version"] == 1

    # discard
    app_client.put(
        "/api/admin/skills/classify/draft", json={"content_md": "x"}, headers=_bearer(1)
    )
    r = app_client.delete("/api/admin/skills/classify/draft", headers=_bearer(1))
    assert r.status_code == 200 and r.json()["has_draft"] is False


def test_promote_no_draft_409(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.post(
        "/api/admin/skills/classify/draft/promote", json={}, headers=_bearer(1)
    )
    assert r.status_code == 409


def test_validate_unsupported_skill(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.post(
        "/api/admin/skills/dedup/draft/validate", json={}, headers=_bearer(1)
    )
    assert r.status_code == 200
    assert r.json()["supported"] is False


def test_validate_classify_diff_replay(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Source, Ticket
    from app.services.agents.classify import ClassifyResult

    world.add(Source(code="ksm", name="KSM"))
    world.add(
        Ticket(
            id=900,
            short_code="TKT-900",
            source_code="ksm",
            source_ticket_id="k-900",
            type="Raw",
            status="received",
            title="报错",
            body="点击开具报错",
        )
    )
    world.commit()
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    app_client.put(
        "/api/admin/skills/classify/draft",
        json={"content_md": "新规则"},
        headers=_bearer(1),
    )

    calls = {"n": 0}

    def fake_payload(**kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        # current → Operation；draft → Bug_fix（制造差异）
        t = "Operation" if kw.get("system_prompt_override") != "新规则" else "Bug_fix"
        return ClassifyResult(
            type=t, confidence=0.9, reason="r", cost_usd=0.0, model="fake", raw={}
        )

    monkeypatch.setattr(
        "app.services.skills.draft_validator.classify_payload", fake_payload, raising=False
    )
    # validator 内部是 from ... import，monkeypatch 模块内引用
    import app.services.agents.classify as clf

    monkeypatch.setattr(clf, "classify_payload", fake_payload)

    r = app_client.post(
        "/api/admin/skills/classify/draft/validate", json={"sample": 5}, headers=_bearer(1)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is True
    assert body["sample_size"] == 1
    assert body["changed_count"] == 1
    row = body["rows"][0]
    assert row["current_type"] == "Operation" and row["draft_type"] == "Bug_fix"
