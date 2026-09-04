"""研发类/运营类工单毕业时处理人传播单测.

入库即分派改造后，处理人分派发生在 ticket 入库阶段（dispatch_handler，见
test_dispatch_engine.py），毕业（ensure_hub_issue_for_ticket）不再重新分派，
只是把 ticket.handler_user_id 传播到 hub 层（Operation→op_handler_user_id）。
这里直接在 fixture 上设置 handler_user_id 模拟「入库时已分派」的结果。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, Ticket, User
from app.services.hub_issues.creator import (
    create_hub_issue_for_ticket_auto,
    ensure_hub_issue_for_ticket,
)

_next_uid = 0


def _seed_user(db: Session, name: str) -> User:
    global _next_uid
    _next_uid += 1
    u = User(
        feishu_uid=f"ou_dispatch_{_next_uid}",
        name=name,
        email=f"{name}@x.com",
        role="assignee",
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _seed_classified_ticket(
    db: Session, *, ptype: str, reporter_uid: int, handler_uid: int | None = None
) -> Ticket:
    """handler_uid=None 模拟「入库时无匹配分派规则」；传入值模拟「入库时已分派」。"""
    if db.query(Source).filter_by(code="ksm").first() is None:
        db.add(Source(code="ksm", name="KSM"))
        db.flush()
    t = Ticket(
        short_code=f"TKT-{ptype}-{reporter_uid}",
        source_code="ksm",
        source_ticket_id=f"ksm-{reporter_uid}",
        type="Raw",
        status="received",
        title="开票报错",
        body="点击开票提示系统异常",
        predicted_type=ptype,
        predicted_confidence=0.95,
        assigned_user_id=reporter_uid,  # 入库责任人
        handler_user_id=handler_uid,  # 入库时 dispatch_handler 已分派的处理人
    )
    db.add(t)
    db.flush()
    return t


@pytest.mark.parametrize("ptype", ["Bug_fix", "Demand"])
def test_dev_class_handler_propagates_not_assigned(db_session: Session, ptype: str) -> None:
    """研发类：ticket.handler_user_id 已在入库时分派好，毕业不改写它；
    hub.assigned_user_id 保持入库责任人不变。"""
    reporter = _seed_user(db_session, f"reporter_{ptype}")
    handler = _seed_user(db_session, f"handler_{ptype}")
    t = _seed_classified_ticket(
        db_session, ptype=ptype, reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.created is True
    assert result.dispatch_missed is False
    hub = db_session.get(HubIssue, result.hub_issue_id)
    db_session.refresh(t)
    assert t.handler_user_id == handler.id  # 入库时已分派，毕业不改写
    assert hub.assigned_user_id == reporter.id  # 责任人不被覆盖
    assert hub.op_handler_user_id is None  # 研发类不写 op_handler_user_id


def test_dev_class_no_handler_sets_dispatch_missed_flag(db_session: Session) -> None:
    """研发类入库时未分派到处理人（handler_user_id 为空）→ dispatch_missed=True，
    assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter2")
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.dispatch_missed is True
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.assigned_user_id == reporter.id  # 无分派 → 保持责任人不变


def test_operation_handler_propagates_to_op_handler_not_override(db_session: Session) -> None:
    """Operation：ticket.handler_user_id 传播到 hub.op_handler_user_id，
    assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter3")
    handler = _seed_user(db_session, "ophandler")
    t = _seed_classified_ticket(
        db_session, ptype="Operation", reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.op_handler_user_id == handler.id
    assert hub.assigned_user_id == reporter.id  # Operation 不覆盖责任人
    assert result.dispatch_missed is False  # Operation 无结果也不设 missed


def test_auto_dispatch_missed_gate_off_marks_pending_no_linear(
    db_session: Session, monkeypatch
) -> None:
    """闸门①关 + 研发类入库未分派 → status=pending，不调 push_hub_issue_to_linear。"""
    reporter = _seed_user(db_session, "reporter4")
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id

    # create_hub_issue_for_ticket_auto 自开 session，用 monkeypatch 让它复用测试 session
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    # 防 session 被内部 close 掉影响断言
    monkeypatch.setattr(db_session, "close", lambda: None)
    # 闸门①关：dispatch_missed 才走 pending 转人工
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": False,
                "require_review_before_linear": False,
                "hub_dedup_enabled": False,
            },
        )(),
    )

    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result is not None and result.dispatch_missed is True
    mock_push.assert_not_called()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending"


def test_auto_dispatch_missed_gate_on_goes_pending_review(db_session: Session, monkeypatch) -> None:
    """闸门①开 + 研发类入库未分派 → 仍先停 pending_review（分类确认优先于分派缺人转人工）。

    回归 bug：dispatch_missed 提前分流曾绕过闸门①，让分派缺人的研发类工单
    直接进 pending 队列而非 pending_review 分类确认队列（TKT-005963）。
    """
    reporter = _seed_user(db_session, "reporter4b")
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id

    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": True,
                "require_review_before_linear": True,
                "hub_dedup_enabled": False,
            },
        )(),
    )

    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result is not None and result.dispatch_missed is True
    mock_push.assert_not_called()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"  # 闸门①优先，不提前转 pending


def test_auto_dispatch_hit_with_review_goes_pending_review(
    db_session: Session, monkeypatch
) -> None:
    """auto 路径入库已分派 + review 开 → pending_review（不误判为 dispatch pending）。"""
    reporter = _seed_user(db_session, "reporter5")
    handler = _seed_user(db_session, "handler5")
    t = _seed_classified_ticket(
        db_session, ptype="Bug_fix", reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()
    ticket_id = t.id

    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "require_review_before_linear": True,
                "gate_classify_enabled": True,
                "hub_dedup_enabled": False,
            },
        )(),
    )

    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result.dispatch_missed is False
    mock_push.assert_not_called()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"


def test_gate_classify_on_operation_stays_pending_review(db_session, monkeypatch) -> None:
    """闸门①开 + accuracy_mode!='review'：Operation 毕业也停 pending_review，不进自动答复链。"""
    reporter = _seed_user(db_session, "rep_op_gate")
    t = _seed_classified_ticket(db_session, ptype="Operation", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": True,
                "hub_dedup_enabled": False,
                "require_review_before_linear": True,
                "operation_answer_accuracy_mode": "off",
            },
        )(),
    )
    result = create_hub_issue_for_ticket_auto(ticket_id)
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"


def test_gate_classify_on_operation_accuracy_review_skips_pending_review(
    db_session, monkeypatch
) -> None:
    """闸门①开 + accuracy_mode=='review'：Operation 跳过 pending_review，直接进
    drain 扫描范围（status='created'，op_status/op_handler 已在毕业时预置）。"""
    reporter = _seed_user(db_session, "rep_op_review")
    t = _seed_classified_ticket(db_session, ptype="Operation", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": True,
                "hub_dedup_enabled": False,
                "require_review_before_linear": True,
                "operation_answer_accuracy_mode": "review",
            },
        )(),
    )
    result = create_hub_issue_for_ticket_auto(ticket_id)
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "created"
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"


def test_gate_classify_off_devclass_pushes(db_session, monkeypatch) -> None:
    """闸门①关 + 模块负责人确定 → 研发类自动推 Linear。"""
    reporter = _seed_user(db_session, "rep_off")
    handler = _seed_user(db_session, "h_off")
    t = _seed_classified_ticket(
        db_session, ptype="Bug_fix", reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": False,
                "hub_dedup_enabled": False,
                "require_review_before_linear": False,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.hub_issues.creator.peek_module_owner", lambda *a, **k: handler
    )
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mp:
        create_hub_issue_for_ticket_auto(ticket_id)
    mp.assert_called_once()


def test_gate_classify_off_module_owner_unresolved_parks_pending_linear_review(
    db_session, monkeypatch
) -> None:
    """闸门①关 + 模块负责人不确定 → 停 pending_linear_review，不直推 Linear。"""
    reporter = _seed_user(db_session, "rep_indep_on")
    handler = _seed_user(db_session, "h_indep_on")
    t = _seed_classified_ticket(
        db_session, ptype="Bug_fix", reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": False,
                "hub_dedup_enabled": False,
                "require_review_before_linear": False,
            },
        )(),
    )
    monkeypatch.setattr("app.services.hub_issues.creator.peek_module_owner", lambda *a, **k: None)
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mp:
        result = create_hub_issue_for_ticket_auto(ticket_id)
    mp.assert_not_called()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_linear_review"
    # 责任人默认值 = 处理人（入库时 dispatch_handler 已写入 ticket.handler_user_id）
    assert hub.owner_user_id == handler.id


def test_gate_classify_off_module_owner_resolved_pushes(db_session, monkeypatch) -> None:
    """闸门①关 + 模块负责人确定 → 直推 Linear（status=created）。"""
    reporter = _seed_user(db_session, "rep_indep_off")
    handler = _seed_user(db_session, "h_indep_off")
    t = _seed_classified_ticket(
        db_session, ptype="Bug_fix", reporter_uid=reporter.id, handler_uid=handler.id
    )
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gate_classify_enabled": False,
                "hub_dedup_enabled": False,
                "require_review_before_linear": False,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.hub_issues.creator.peek_module_owner", lambda *a, **k: handler
    )
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mp:
        result = create_hub_issue_for_ticket_auto(ticket_id)
    mp.assert_called_once()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "created"
