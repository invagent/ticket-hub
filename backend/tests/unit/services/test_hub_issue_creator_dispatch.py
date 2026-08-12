"""研发类工单走分派引擎单测。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import (
    DispatchAssignee,
    DispatchRule,
    HubIssue,
    Source,
    Ticket,
    User,
)
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


def _seed_dispatch_rule(db: Session, assignee_user_id: int) -> DispatchRule:
    """一条 match-all（空维度全通配）count 规则，命中任意 hub。"""
    rule = DispatchRule(
        name="all",
        priority=1,
        is_active=True,
        match_sources=[],
        match_product_lines=[],
        match_modules=[],
        match_sla=[],
        dispatch_mode="count",
    )
    db.add(rule)
    db.flush()
    db.add(
        DispatchAssignee(
            rule_id=rule.id,
            user_id=assignee_user_id,
            tier="main",
            alloc_value=1,
            daily_cap=None,
            is_active=True,
        )
    )
    db.flush()
    return rule


def _seed_classified_ticket(db: Session, *, ptype: str, reporter_uid: int) -> Ticket:
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
    )
    db.add(t)
    db.flush()
    return t


@pytest.mark.parametrize("ptype", ["Bug_fix", "Demand"])
def test_dev_class_dispatch_writes_handler_not_assigned(db_session: Session, ptype: str) -> None:
    """研发类命中分派 → 写 ticket.handler_user_id（HubIssue 无此列）；
    hub.assigned_user_id 保持入库责任人不变。"""
    reporter = _seed_user(db_session, f"reporter_{ptype}")
    handler = _seed_user(db_session, f"handler_{ptype}")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype=ptype, reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.created is True
    assert result.dispatch_missed is False
    hub = db_session.get(HubIssue, result.hub_issue_id)
    db_session.refresh(t)
    assert t.handler_user_id == handler.id  # 分派写 ticket 层 handler
    assert hub.assigned_user_id == reporter.id  # 责任人不被覆盖
    assert hub.op_handler_user_id is None  # 研发类不写 op_handler_user_id


def test_dev_class_dispatch_missed_sets_flag(db_session: Session) -> None:
    """研发类无匹配规则+无兜底 → dispatch_missed=True，assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter2")
    # 不建任何 DispatchRule → 分派无结果
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.dispatch_missed is True
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.assigned_user_id == reporter.id  # 无分派 → 保持责任人不变


def test_operation_dispatch_writes_op_handler_not_override(db_session: Session) -> None:
    """Operation 命中分派 → 写 op_handler_user_id，assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter3")
    handler = _seed_user(db_session, "ophandler")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype="Operation", reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.op_handler_user_id == handler.id
    assert hub.assigned_user_id == reporter.id  # Operation 不覆盖责任人
    assert result.dispatch_missed is False  # Operation 无结果也不设 missed


def test_auto_dispatch_missed_marks_pending_no_linear(db_session: Session, monkeypatch) -> None:
    """auto 路径研发类分派无结果 → status=pending，不调 push_hub_issue_to_linear。"""
    reporter = _seed_user(db_session, "reporter4")
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id

    # create_hub_issue_for_ticket_auto 自开 session，用 monkeypatch 让它复用测试 session
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    # 防 session 被内部 close 掉影响断言
    monkeypatch.setattr(db_session, "close", lambda: None)

    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result is not None and result.dispatch_missed is True
    mock_push.assert_not_called()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending"


def test_auto_dispatch_hit_with_review_goes_pending_review(
    db_session: Session, monkeypatch
) -> None:
    """auto 路径命中分派 + review 开 → pending_review（不误判为 dispatch pending）。"""
    reporter = _seed_user(db_session, "reporter5")
    handler = _seed_user(db_session, "handler5")
    _seed_dispatch_rule(db_session, handler.id)
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
    """闸门①开：Operation 毕业也停 pending_review，不进自动答复链。"""
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
                "gate_linear_push_enabled": True,
            },
        )(),
    )
    result = create_hub_issue_for_ticket_auto(ticket_id)
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"


def test_gate_classify_off_devclass_pushes(db_session, monkeypatch) -> None:
    """闸门①关：研发类命中分派仍走现状分流，直接推 Linear。"""
    reporter = _seed_user(db_session, "rep_off")
    handler = _seed_user(db_session, "h_off")
    _seed_dispatch_rule(db_session, handler.id)
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
                "gate_classify_enabled": False,
                "hub_dedup_enabled": False,
                "require_review_before_linear": False,
                "gate_linear_push_enabled": False,
            },
        )(),
    )
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mp:
        create_hub_issue_for_ticket_auto(ticket_id)
    mp.assert_called_once()
