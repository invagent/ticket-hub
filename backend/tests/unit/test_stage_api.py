"""ADR-0017 Task 1.5：stage 在 /api/tickets、/api/hub-issues、/history 的暴露与筛选。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Source, Ticket, User


def _bearer(user_id: int = 1, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="admin"))
    db_session.flush()
    hub_op = HubIssue(
        id=10,
        short_code="HUB-000010",
        type="Operation",
        title="op",
        status="created",
        op_status="processing",
    )
    hub_dev = HubIssue(
        id=20,
        short_code="HUB-000020",
        type="Bug_fix",
        title="bug",
        status="in_progress",
        linear_status="In Review",
    )
    db_session.add_all([hub_op, hub_dev])
    db_session.flush()
    db_session.add_all(
        [
            Ticket(
                id=100,
                short_code="TKT-000100",
                source_code="ksm",
                source_ticket_id="k1",
                type="Raw",
                status="received",
                title="a",
            ),
            Ticket(
                id=101,
                short_code="TKT-000101",
                source_code="ksm",
                source_ticket_id="k2",
                type="Raw",
                status="received",
                title="b",
                hub_issue_id=10,
                predicted_type="Operation",
            ),
            Ticket(
                id=102,
                short_code="TKT-000102",
                source_code="ksm",
                source_ticket_id="k3",
                type="Raw",
                status="in_progress",
                title="c",
                hub_issue_id=20,
                predicted_type="Bug_fix",
            ),
        ]
    )
    db_session.commit()
    return db_session


def test_list_exposes_stage_and_label(app_client, world: Session) -> None:
    resp = app_client.get("/api/tickets", headers=_bearer())
    assert resp.status_code == 200
    by_id = {it["id"]: it for it in resp.json()["items"]}
    assert (by_id[100]["stage"], by_id[100]["stage_label"]) == ("received", "已接收")
    assert (by_id[101]["stage"], by_id[101]["stage_label"]) == ("processing", "处理中")
    assert (by_id[102]["stage"], by_id[102]["stage_label"]) == ("dev_review", "测试中")
    assert by_id[102]["stage_changed_at"] is not None


def test_list_filter_by_stage(app_client, world: Session) -> None:
    resp = app_client.get("/api/tickets", params={"stage": "dev_review"}, headers=_bearer())
    assert [it["id"] for it in resp.json()["items"]] == [102]
    resp = app_client.get(
        "/api/tickets", params=[("stages", "received"), ("stages", "processing")], headers=_bearer()
    )
    assert sorted(it["id"] for it in resp.json()["items"]) == [100, 101]
    assert resp.json()["total"] == 2


def test_detail_exposes_stage(app_client, world: Session) -> None:
    resp = app_client.get("/api/tickets/102", headers=_bearer())
    assert resp.status_code == 200
    assert resp.json()["stage"] == "dev_review"
    assert resp.json()["stage_label"] == "测试中"


def test_hub_list_and_detail_expose_stage(app_client, world: Session) -> None:
    resp = app_client.get("/api/hub-issues", headers=_bearer())
    assert resp.status_code == 200
    by_id = {it["id"]: it for it in resp.json()["items"]}
    assert (by_id[10]["stage"], by_id[10]["stage_label"]) == ("processing", "处理中")
    assert (by_id[20]["stage"], by_id[20]["stage_label"]) == ("dev_review", "测试中")
    resp = app_client.get("/api/hub-issues/20", headers=_bearer())
    assert resp.json()["stage_label"] == "测试中"


def test_history_includes_stage_events(app_client, world: Session) -> None:
    hub = world.get(HubIssue, 10)
    assert hub is not None
    world.info["stage_actor"] = "agent:operation_answer"
    hub.op_status = "answered"
    world.commit()

    resp = app_client.get("/api/tickets/101/history", headers=_bearer())
    assert resp.status_code == 200
    stage_events = [e for e in resp.json()["items"] if e["kind"] == "stage"]
    assert len(stage_events) == 1
    ev = stage_events[0]
    assert (ev["from_status"], ev["to_status"]) == ("processing", "answered")
    assert (ev["from_status_zh"], ev["to_status_zh"]) == ("处理中", "已答复")
    assert ev["changed_by"] == "agent:operation_answer"
    assert ev["metadata_"] == {"kind": "stage"}
