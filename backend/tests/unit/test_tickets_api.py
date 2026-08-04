"""Tests for /api/tickets and /api/hub-issues endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Source, Ticket, User


def _bearer(user_id: int = 1, *, role: str = "assignee") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    db_session.add(User(id=2, feishu_uid="ou_b", name="bob", role="assignee"))
    db_session.commit()

    # 3 hub issues
    db_session.add_all(
        [
            HubIssue(
                id=10,
                short_code="HUB-OP",
                type="Operation",
                title="op-issue",
                status="waiting_reply",
                op_status="processing",
                assigned_user_id=1,
                module="应付",
            ),
            HubIssue(
                id=20,
                short_code="HUB-BUG",
                type="Bug_fix",
                title="bug-issue",
                status="in_progress",
                assigned_user_id=2,
                module="应付",
            ),
            HubIssue(
                id=30,
                short_code="HUB-DEMAND",
                type="Demand",
                title="demand-issue",
                status="scheduled",
                assigned_user_id=None,
                module="应收",
            ),
        ]
    )
    db_session.flush()

    # 5 tickets — varying source / status / assignee / hub_issue link
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            Ticket(
                id=100,
                short_code="TKT-1",
                source_code="ksm",
                source_ticket_id="ksm-1",
                type="Raw",
                status="received",
                title="ksm a",
                module="应付",
                assigned_user_id=1,
                received_at=base,
            ),
            Ticket(
                id=101,
                short_code="TKT-2",
                source_code="ksm",
                source_ticket_id="ksm-2",
                type="Raw",
                status="linked",
                title="ksm b",
                hub_issue_id=10,
                module="应付",
                assigned_user_id=1,
                received_at=base + timedelta(minutes=1),
            ),
            Ticket(
                id=102,
                short_code="TKT-3",
                source_code="zhichi",
                source_ticket_id="z-1",
                type="Raw",
                status="received",
                title="zhichi a",
                assigned_user_id=2,
                received_at=base + timedelta(minutes=2),
            ),
            Ticket(
                id=103,
                short_code="TKT-4",
                source_code="ksm",
                source_ticket_id="ksm-3",
                type="Raw",
                status="done",
                title="ksm done",
                hub_issue_id=20,
                received_at=base - timedelta(hours=1),
            ),
            # soft-deleted: should NEVER appear in list/detail
            Ticket(
                id=104,
                short_code="TKT-DEL",
                source_code="ksm",
                source_ticket_id="ksm-deleted",
                type="Raw",
                status="received",
                title="deleted",
                deleted_at=base,
                received_at=base,
            ),
        ]
    )
    db_session.commit()
    return db_session


# ============================================================================
# /api/tickets
# ============================================================================


def test_list_tickets_requires_auth(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/tickets").status_code == 401


def test_list_tickets_default(app_client: TestClient, world: Session) -> None:
    resp = app_client.get("/api/tickets", headers=_bearer())
    assert resp.status_code == 200
    body = resp.json()
    # 4 alive (excludes the soft-deleted one)
    assert body["total"] == 4
    assert len(body["items"]) == 4
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert body["has_more"] is False
    # ordered by received_at desc
    short_codes = [it["short_code"] for it in body["items"]]
    assert short_codes[0] == "TKT-3"
    assert short_codes[-1] == "TKT-4"  # earliest received_at


def test_list_tickets_op_status(app_client: TestClient, world: Session) -> None:
    # TKT-2 挂 Operation hub(op_status=processing) → 带出；TKT-4 挂 Bug_fix hub → op_status 空
    resp = app_client.get("/api/tickets", headers=_bearer())
    by_code = {it["short_code"]: it for it in resp.json()["items"]}
    assert by_code["TKT-2"]["op_status"] == "processing"
    assert by_code["TKT-4"]["op_status"] is None  # 研发类 hub 无 op_status
    assert by_code["TKT-3"]["op_status"] is None  # 未挂 hub


def test_list_tickets_filter_source(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?source_code=ksm", headers=_bearer())
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(it["source_code"] == "ksm" for it in items)
    assert {it["short_code"] for it in items} == {"TKT-1", "TKT-2", "TKT-4"}


def test_list_tickets_filter_status(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?status=received", headers=_bearer())
    assert r.json()["total"] == 2
    assert {it["short_code"] for it in r.json()["items"]} == {"TKT-1", "TKT-3"}


def test_list_tickets_filter_assigned_user(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?assigned_user_id=1", headers=_bearer())
    assert r.json()["total"] == 2  # TKT-1 + TKT-2
    assert all(it["assigned_user_id"] == 1 for it in r.json()["items"])


def test_list_tickets_filter_unassigned_only(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?unassigned_only=true", headers=_bearer())
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["short_code"] == "TKT-4"


def test_list_tickets_filter_hub_issue(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?hub_issue_id=10", headers=_bearer())
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["short_code"] == "TKT-2"


def test_list_tickets_pagination(app_client: TestClient, world: Session) -> None:
    r1 = app_client.get("/api/tickets?page=1&page_size=2", headers=_bearer())
    r2 = app_client.get("/api/tickets?page=2&page_size=2", headers=_bearer())
    assert r1.json()["has_more"] is True
    assert len(r1.json()["items"]) == 2
    assert r2.json()["has_more"] is False
    assert len(r2.json()["items"]) == 2
    # No overlap
    p1 = {it["id"] for it in r1.json()["items"]}
    p2 = {it["id"] for it in r2.json()["items"]}
    assert p1.isdisjoint(p2)


def test_list_tickets_excludes_soft_deleted(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets?source_code=ksm", headers=_bearer())
    short_codes = {it["short_code"] for it in r.json()["items"]}
    assert "TKT-DEL" not in short_codes


def test_get_ticket_returns_full_detail(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/tickets/100", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 100
    assert body["short_code"] == "TKT-1"
    # detail includes the body fields
    assert "body" in body
    assert "source_payload" in body


def test_get_ticket_unknown_returns_404(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/tickets/9999", headers=_bearer()).status_code == 404


def test_get_ticket_soft_deleted_returns_404(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/tickets/104", headers=_bearer()).status_code == 404


# ---- 工单列表优化：多选筛选 + 新字段（product_name/reject_count/children_count）----


@pytest.fixture
def world2(db_session: Session) -> Session:
    """独立种子：覆盖 predicted_type 多选、product_line 名称、reject_count、拆分子单数。"""
    from app.models import ProductLine

    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-fapiao", name="发票云"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    db_session.add(User(id=2, feishu_uid="ou_b", name="bob", role="assignee"))
    # 被驳回过 2 次的 Operation hub
    db_session.add(
        HubIssue(
            id=50,
            short_code="HUB-RJ",
            type="Operation",
            title="rj",
            status="waiting_reply",
            op_status="answered",
            reject_count=2,
        )
    )
    db_session.flush()
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    db_session.add_all(
        [
            # Operation：挂被驳回 hub + 有产品线
            Ticket(
                id=200,
                short_code="TKT-A",
                source_code="ksm",
                source_ticket_id="a",
                type="Raw",
                status="linked",
                title="op",
                predicted_type="Operation",
                product_line_code="cloud-fapiao",
                hub_issue_id=50,
                assigned_user_id=1,
                received_at=base,
            ),
            # Bug_fix
            Ticket(
                id=201,
                short_code="TKT-B",
                source_code="ksm",
                source_ticket_id="b",
                type="Raw",
                status="received",
                title="bug",
                predicted_type="Bug_fix",
                assigned_user_id=2,
                received_at=base + timedelta(minutes=1),
            ),
            # Parent：拆了 3 个子单
            Ticket(
                id=202,
                short_code="TKT-P",
                source_code="ksm",
                source_ticket_id="p",
                type="Parent",
                status="split",
                title="parent",
                predicted_type="Demand",
                children_ticket_ids=[301, 302, 303],
                received_at=base + timedelta(minutes=2),
            ),
        ]
    )
    db_session.commit()
    return db_session


def test_filter_predicted_types_multi(app_client: TestClient, world2: Session) -> None:
    """类型多选：predicted_types=Operation&predicted_types=Bug_fix。"""
    r = app_client.get(
        "/api/tickets?predicted_types=Operation&predicted_types=Bug_fix", headers=_bearer()
    )
    assert r.status_code == 200
    assert {it["short_code"] for it in r.json()["items"]} == {"TKT-A", "TKT-B"}


def test_filter_assigned_user_ids_multi(app_client: TestClient, world2: Session) -> None:
    """处理人多选：assigned_user_ids=1&assigned_user_ids=2。"""
    r = app_client.get("/api/tickets?assigned_user_ids=1&assigned_user_ids=2", headers=_bearer())
    assert {it["short_code"] for it in r.json()["items"]} == {"TKT-A", "TKT-B"}


def test_summary_product_name(app_client: TestClient, world2: Session) -> None:
    """主产品名称：product_line_code=cloud-fapiao → name=发票云。"""
    r = app_client.get("/api/tickets", headers=_bearer())
    by = {it["short_code"]: it for it in r.json()["items"]}
    assert by["TKT-A"]["product_name"] == "发票云"
    assert by["TKT-B"]["product_name"] is None  # 无产品线


def test_summary_reject_count(app_client: TestClient, world2: Session) -> None:
    """驳回次数：挂 reject_count=2 的 hub → 2；无 hub → 0。"""
    r = app_client.get("/api/tickets", headers=_bearer())
    by = {it["short_code"]: it for it in r.json()["items"]}
    assert by["TKT-A"]["reject_count"] == 2
    assert by["TKT-B"]["reject_count"] == 0


def test_summary_children_count(app_client: TestClient, world2: Session) -> None:
    """关联任务数：Parent 有 3 子单 → 3；单问题 → 1。"""
    r = app_client.get("/api/tickets", headers=_bearer())
    by = {it["short_code"]: it for it in r.json()["items"]}
    assert by["TKT-P"]["children_count"] == 3
    assert by["TKT-B"]["children_count"] == 1


# ============================================================================
# /api/hub-issues
# ============================================================================


def test_list_hub_issues_requires_auth(app_client: TestClient) -> None:
    assert app_client.get("/api/hub-issues").status_code == 401


def test_list_hub_issues_default(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/hub-issues", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    types = [it["type"] for it in body["items"]]
    assert set(types) == {"Operation", "Bug_fix", "Demand"}


def test_list_hub_issues_filter_type(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/hub-issues?type=Bug_fix", headers=_bearer())
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["short_code"] == "HUB-BUG"


def test_list_hub_issues_filter_status(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/hub-issues?status=waiting_reply", headers=_bearer())
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["short_code"] == "HUB-OP"


def test_list_hub_issues_filter_module(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/hub-issues?module=应付", headers=_bearer())
    assert r.json()["total"] == 2


def test_get_hub_issue_includes_linked_tickets(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/hub-issues/10", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 10
    assert body["short_code"] == "HUB-OP"
    # TKT-2 is linked to HUB-OP
    linked = body["linked_tickets"]
    assert len(linked) == 1
    assert linked[0]["short_code"] == "TKT-2"


def test_get_hub_issue_no_linked_tickets(app_client: TestClient, world: Session) -> None:
    """HUB-DEMAND (id=30) has no linked tickets."""
    r = app_client.get("/api/hub-issues/30", headers=_bearer())
    assert r.status_code == 200
    assert r.json()["linked_tickets"] == []


def test_get_hub_issue_unknown_returns_404(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/hub-issues/9999", headers=_bearer()).status_code == 404
