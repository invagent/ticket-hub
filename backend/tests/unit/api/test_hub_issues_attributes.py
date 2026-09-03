"""工单参数编辑端点单测（PATCH /attributes + 模块下拉 + op_handler_user_id）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue, Ticket


def _bearer(user_id=1, *, name="carol", role="supervisor"):
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


def _mk(db, hub_id=200, *, type_="Bug_fix", status="pending_review", op_status=None, handler_uid=None):
    db.add(
        HubIssue(
            id=hub_id,
            short_code=f"HUB-{hub_id}",
            type=type_,
            title="t",
            canonical_body="b",
            status=status,
            op_status=op_status,
            op_handler_user_id=handler_uid,
            product_line_code="pl-old",
            module="m-old",
        )
    )
    db.flush()
    db.add(
        Ticket(
            id=hub_id,
            short_code=f"TKT-{hub_id}",
            source_code="ksm",
            source_ticket_id=f"k{hub_id}",
            type="Raw",
            status="received",
            hub_issue_id=hub_id,
            predicted_type=type_,
        )
    )
    db.commit()
    return hub_id


def test_attributes_change_type_syncs_ticket_and_audits(app_client: TestClient, db_session: Session):
    hid = _mk(db_session)
    r = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Demand"}, headers=_bearer())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "Demand" and body["updated_ticket_count"] == 1
    db_session.expire_all()
    assert db_session.get(HubIssue, hid).type == "Demand"
    t = db_session.query(Ticket).filter(Ticket.hub_issue_id == hid).first()
    assert t.predicted_type == "Demand"
    dec = (
        db_session.query(AgentDecision)
        .filter(
            AgentDecision.subject_type == "ticket",
            AgentDecision.subject_id == t.id,
            AgentDecision.decision_type == "classify_type",
        )
        .first()
    )
    assert dec is not None and dec.proposal["human_confirmed"] is True


def test_attributes_pending_review_type_edit_does_not_advance_status(
    app_client: TestClient, db_session: Session
):
    """待确认分类(pending_review)的 hub 改类型下拉框只是编辑草稿，不能替
    confirm-classification/graduate 抢先决定分流——否则光切类型还没点确认
    按钮，hub 就被推进 created/pending_linear_review。"""
    hid = _mk(db_session, hub_id=213, type_="Bug_fix", status="pending_review")
    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"type": "Operation"}, headers=_bearer()
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.type == "Operation"
    assert h.status == "pending_review"
    assert h.op_status is None


def test_attributes_change_product_line_and_module(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=201)
    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes",
        json={"product_line_code": "cloud-fapiao", "module": "开票管理"},
        headers=_bearer(),
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.product_line_code == "cloud-fapiao" and h.module == "开票管理"
    from app.models import Module, ProductLine

    assert db_session.query(ProductLine).filter_by(code="cloud-fapiao").first() is not None
    assert (
        db_session.query(Module).filter_by(product_line_code="cloud-fapiao", name="开票管理").first()
        is not None
    )


def test_attributes_no_linear_push_on_type_change(app_client: TestClient, db_session: Session):
    """改 type 会规整 status/op_status（见 test_attributes_into_operation_* 等），
    但绝不主动推 Linear/走 outbox——研发类改判后仍需人工 repush-linear 把关。"""
    from app.models import SyncOutbox

    hid = _mk(db_session, hub_id=202, type_="Bug_fix", status="pending_review")
    app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Operation"}, headers=_bearer())
    db_session.expire_all()
    assert db_session.query(SyncOutbox).filter_by(hub_issue_id=hid).count() == 0


def test_attributes_into_operation_sets_op_status_and_dispatches(
    app_client: TestClient, db_session: Session
):
    """TKT-006584 教训：Bug_fix/pending_linear_review → Operation 时，之前会卡在
    pending_linear_review 且 op_status 空的孤儿态。现在应回炉答复链并预分配处理人。"""
    from app.models import DispatchAssignee, DispatchRule, User

    hid = _mk(db_session, hub_id=210, type_="Bug_fix", status="pending_linear_review")
    db_session.add(User(id=41, feishu_uid="ou_op41", name="op41", role="assignee"))
    rule = DispatchRule(
        name="all",
        match_sources=[],
        match_product_lines=[],
        match_modules=[],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=100,
        is_active=True,
    )
    db_session.add(rule)
    db_session.flush()
    db_session.add(
        DispatchAssignee(rule_id=rule.id, user_id=41, daily_cap=20, tier="main", is_active=True)
    )
    db_session.commit()

    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"type": "Operation"}, headers=_bearer()
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.type == "Operation"
    assert h.status == "created"
    assert h.op_status == "processing"
    assert h.op_handler == "agent"


def test_attributes_into_dev_without_module_owner_sets_pending_linear_review(
    app_client: TestClient, db_session: Session
):
    """Operation → Bug_fix/Demand 且模块负责人不确定 → 停 pending_linear_review
    待人工选人推送（不主动推 Linear）。"""
    hid = _mk(db_session, hub_id=211, type_="Operation", status="created", op_status="processing")
    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"type": "Bug_fix"}, headers=_bearer()
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.type == "Bug_fix"
    assert h.status == "pending_linear_review"
    assert h.op_status is None


def test_attributes_into_internal_task_clears_orphaned_pending_linear_review(
    app_client: TestClient, db_session: Session
):
    """pending_linear_review 是研发类专属；改成 Internal_task 后不能继续卡在这个
    状态（既不进研发待推队列展示，也不会被任何自动链捡起）。"""
    hid = _mk(db_session, hub_id=212, type_="Bug_fix", status="pending_linear_review")
    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"type": "Internal_task"}, headers=_bearer()
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.type == "Internal_task"
    assert h.status == "created"


def test_attributes_rejected_on_closed(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=203, type_="Operation", status="created", op_status="closed")
    r = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Demand"}, headers=_bearer())
    assert r.status_code == 409, r.text


def test_attributes_operation_to_dev_clears_operation_fields(
    app_client: TestClient, db_session: Session
):
    """Operation → 研发类改 type 时清 Operation 专属字段，避免 ck_hub_issues_operation_fields
    约束冲突（非 Operation 要求 reply_content/reply_authored_by 为 NULL）。"""
    hid = _mk(db_session, hub_id=206, type_="Operation", status="created", op_status="processing")
    h = db_session.get(HubIssue, hid)
    h.reply_content = "草稿答复"
    h.reply_authored_by = "agent:ai_cs:draft"
    h.op_handler = "agent"
    db_session.commit()

    r = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"type": "Bug_fix"}, headers=_bearer()
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.type == "Bug_fix"
    assert h.reply_content is None
    assert h.reply_authored_by is None
    assert h.op_status is None
    assert h.op_handler is None
    assert h.op_handler_user_id is None


def test_attributes_handler_allowed_stranger_403(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=204, type_="Operation", status="created", op_status="processing", handler_uid=7)
    # 处理人本人（uid=7）放行
    r_ok = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"module": "x"}, headers=_bearer(7, name="handler", role="assignee")
    )
    assert r_ok.status_code == 200, r_ok.text
    # 路人（uid=8, member）403
    r_no = app_client.patch(
        f"/api/hub-issues/{hid}/attributes", json={"module": "y"}, headers=_bearer(8, name="stranger", role="member")
    )
    assert r_no.status_code == 403, r_no.text


def test_catalog_modules_readable_by_user(app_client: TestClient, db_session: Session):
    from app.models import Module, ProductLine

    db_session.add(ProductLine(code="pl-1", name="产品线1", is_active=True))
    db_session.add(Module(product_line_code="pl-1", name="模块A", is_active=True))
    db_session.add(Module(product_line_code="pl-2", name="模块B", is_active=True))
    db_session.commit()
    r = app_client.get(
        "/api/hub-issues/catalog/modules?product_line_code=pl-1",
        headers=_bearer(9, name="u", role="member"),
    )
    assert r.status_code == 200, r.text
    names = [m["name"] for m in r.json()]
    assert "模块A" in names and "模块B" not in names  # 按产品线过滤


def test_detail_exposes_op_handler_user_id(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=205, type_="Operation", status="created", op_status="processing", handler_uid=42)
    r = app_client.get(f"/api/hub-issues/{hid}", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["op_handler_user_id"] == 42
