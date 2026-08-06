"""Dispatch 引擎 4 表 + hub.op_handler_user_id 建模冒烟。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, HubIssue


def test_dispatch_rule_roundtrip(db_session: Session) -> None:
    r = DispatchRule(
        name="发票云-数电开票",
        match_sources=["ksm"],
        match_product_lines=["cloud-fapiao"],
        match_modules=["数电开票"],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=10,
        is_active=True,
    )
    db_session.add(r)
    db_session.flush()
    a = DispatchAssignee(
        rule_id=r.id, user_id=1, alloc_value=Decimal("1"), daily_cap=20, tier="main", is_active=True
    )
    db_session.add(a)
    db_session.add(DispatchConfig(key="default_operation_assignee", value="9"))
    db_session.add(DispatchLog(hub_issue_id=1, rule_id=r.id, assignee_user_id=1, tier_hit="main"))
    db_session.commit()
    assert r.id is not None and a.daily_cap == 20
    assert db_session.query(DispatchLog).count() == 1


def test_hub_op_handler_user_id_defaults_none(db_session: Session) -> None:
    h = HubIssue(
        short_code="HUB-000001",
        type="Operation",
        status="created",
        title="t",
        product_line_code=None,
        module=None,
    )
    db_session.add(h)
    db_session.commit()
    assert h.op_handler_user_id is None
