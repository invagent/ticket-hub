"""集成点：毕业预分配、resolve_op_handler 回落、drain 口径不变。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    DispatchAssignee,
    DispatchRule,
    HubIssue,
    Source,
    Ticket,
    User,
)
from app.services.hub_issues.creator import ensure_hub_issue_for_ticket
from app.services.hub_issues.op_status import resolve_op_handler


def _user(db: Session, uid: int, name: str, active: bool = True) -> None:
    db.add(User(id=uid, feishu_uid=f"ou_{uid}", name=name, role="assignee", is_active=active))


def _op_ticket(db: Session, n: int, **overrides: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": f"TKT-DISP-{n}",
        "source_code": "ksm",
        "source_ticket_id": f"disp-{n}",
        "type": "Raw",
        "status": "received",
        "title": f"开票咨询 {n}",
        "body": "详细描述",
        "predicted_type": "Operation",
        "product_line_code": "PL_A",
        "module": "MOD_X",
    }
    base.update(overrides)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_resolve_prefers_preassigned(db_session: Session) -> None:
    _user(db_session, 3, "运营小美")
    h = HubIssue(
        short_code="HUB-1", type="Operation", status="created", title="t", op_handler_user_id=3
    )
    db_session.add(h)
    db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "运营小美"


def test_resolve_falls_back_when_no_preassign(db_session: Session) -> None:
    h = HubIssue(
        short_code="HUB-2", type="Operation", status="created", title="t", op_handler_user_id=None
    )
    db_session.add(h)
    db_session.commit()
    # 无 default_pool 配置时 resolve_supervisor_name 返回 "主管"
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"


def test_resolve_falls_back_when_preassigned_inactive(db_session: Session) -> None:
    _user(db_session, 4, "已离职", active=False)
    h = HubIssue(
        short_code="HUB-3", type="Operation", status="created", title="t", op_handler_user_id=4
    )
    db_session.add(h)
    db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"


def test_graduation_preassigns_op_handler_user_id(db_session: Session) -> None:
    """creator 毕业 Operation 时按规则预分配运营（写 op_handler_user_id），
    但 op_handler 名字仍是 'agent'——不打断 drain 自动答复口径。"""
    db_session.add(Source(code="ksm", name="KSM"))
    _user(db_session, 7, "运营阿强")
    rule = DispatchRule(
        name="PL_A 运营池",
        match_sources=[],
        match_product_lines=["PL_A"],
        match_modules=["MOD_X"],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=10,
    )
    db_session.add(rule)
    db_session.flush()
    db_session.add(
        DispatchAssignee(rule_id=rule.id, user_id=7, daily_cap=20, tier="main", is_active=True)
    )
    t = _op_ticket(db_session, 1)

    res = ensure_hub_issue_for_ticket(t.id, created_by="user:test", db=db_session)
    hub = db_session.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.type == "Operation"
    assert hub.op_handler_user_id == 7  # 预分配运营写进去了
    assert hub.op_handler == "agent"  # 名字仍是 agent，drain 口径不受影响


def test_graduation_matches_non_empty_match_sources(db_session: Session) -> None:
    """Bug#1 回归：规则 match_sources=["ksm"]（非空）时，毕业时 ticket 必须已挂
    hub_issue_id，_hub_source_code 才能反查到 source_code='ksm' 命中该规则。
    dispatch 调用若仍在 ticket.hub_issue_id 赋值之前，source 恒为 None，
    非空 match_sources 规则永远匹配不中，本测试会失败。"""
    db_session.add(Source(code="ksm", name="KSM"))
    _user(db_session, 8, "运营小来源")
    rule = DispatchRule(
        name="ksm 来源运营池",
        match_sources=["ksm"],
        match_product_lines=["PL_A"],
        match_modules=["MOD_X"],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=10,
    )
    db_session.add(rule)
    db_session.flush()
    db_session.add(
        DispatchAssignee(rule_id=rule.id, user_id=8, daily_cap=20, tier="main", is_active=True)
    )
    t = _op_ticket(db_session, 3, source_code="ksm")

    res = ensure_hub_issue_for_ticket(t.id, created_by="user:test", db=db_session)
    hub = db_session.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.op_handler_user_id == 8


def test_graduation_no_rule_leaves_op_handler_unassigned(db_session: Session) -> None:
    """无匹配规则时毕业 Operation：op_handler_user_id 留 None，op_handler 仍 'agent'。"""
    db_session.add(Source(code="ksm", name="KSM"))
    t = _op_ticket(db_session, 2)

    res = ensure_hub_issue_for_ticket(t.id, created_by="user:test", db=db_session)
    hub = db_session.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.op_handler_user_id is None
    assert hub.op_handler == "agent"
