"""分派引擎：count 最少者/daily_cap/溢出/兜底、ratio 缺口最大、跨天、边界。

入库即分派改造后 dispatch_handler(db, ticket) 只按来源(+SLA，恒 None)匹配
——match_product_lines/match_modules 暂停使用，DispatchLog 落 ticket_id。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, Ticket, User
from app.services.dispatch.engine import dispatch_handler


def _user(db: Session, uid: int, name: str) -> None:
    db.add(User(id=uid, feishu_uid=f"ou_{uid}", name=name, role="assignee"))


def _ticket(db: Session, **ov) -> Ticket:
    base = {
        "short_code": "TKT-DISP-1",
        "source_code": "ksm",
        "source_ticket_id": "disp-1",
        "type": "Raw",
        "status": "received",
        "title": "t",
        "product_line_code": "cloud-fapiao",
        "module": "数电开票",
    }
    base.update(ov)
    t = Ticket(**base)
    db.add(t)
    db.flush()
    return t


def _rule(db: Session, mode: str = "count", **ov) -> DispatchRule:
    base = {
        "name": "r",
        "match_sources": [],
        "match_product_lines": [],
        "match_modules": [],
        "match_sla": [],
        "dispatch_mode": mode,
        "rule_type": "primary",
        "priority": 100,
        "is_active": True,
    }
    base.update(ov)
    r = DispatchRule(**base)
    db.add(r)
    db.flush()
    return r


def test_count_picks_least_today(db_session: Session) -> None:
    _user(db_session, 1, "张三")
    _user(db_session, 2, "李四")
    r = _rule(db_session)
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=True)
    )
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=2, daily_cap=20, tier="main", is_active=True)
    )
    db_session.add(
        DispatchLog(ticket_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main")
    )  # 张三今日已1
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 2 and res.tier == "main"  # 李四今日0，更少


def test_count_daily_cap_full_goes_overflow(db_session: Session) -> None:
    _user(db_session, 1, "张三")
    _user(db_session, 5, "出王")
    over = _rule(db_session, name="overflow", rule_type="overflow")
    db_session.add(
        DispatchAssignee(rule_id=over.id, user_id=5, daily_cap=None, tier="main", is_active=True)
    )
    db_session.flush()
    main = _rule(db_session, name="main", overflow_rule_id=over.id)
    db_session.add(
        DispatchAssignee(rule_id=main.id, user_id=1, daily_cap=1, tier="main", is_active=True)
    )
    db_session.add(
        DispatchLog(ticket_id=99, rule_id=main.id, assignee_user_id=1, tier_hit="main")
    )  # 已满 1/1
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 5 and res.tier == "overflow"


def test_falls_back_to_config_default(db_session: Session) -> None:
    _user(db_session, 1, "张三")
    _user(db_session, 9, "兜底")
    r = _rule(db_session)
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=1, tier="main", is_active=True)
    )
    db_session.add(
        DispatchLog(ticket_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main")
    )  # 满，无溢出
    db_session.add(DispatchConfig(key="default_operation_assignee", value="9"))
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 9 and res.tier == "default"


def test_no_match_returns_none(db_session: Session) -> None:
    _rule(db_session, match_sources=["zammad"])  # 不命中（ticket 来源是 ksm）
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id is None


def test_match_product_lines_no_longer_filters(db_session: Session) -> None:
    """match_product_lines/match_modules 已暂停使用：即便规则配了跟 ticket 不符的
    产品线/模块值，只要来源命中（空 match_sources=不限），照样命中该规则。"""
    _user(db_session, 1, "张三")
    r = _rule(db_session, match_product_lines=["other-product"], match_modules=["other-module"])
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=True)
    )
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 1


def test_ratio_picks_largest_gap(db_session: Session) -> None:
    _user(db_session, 1, "赵六")
    _user(db_session, 2, "钱七")
    r = _rule(db_session, mode="ratio")
    db_session.add(
        DispatchAssignee(
            rule_id=r.id, user_id=1, alloc_value=Decimal("5"), tier="main", is_active=True
        )
    )
    db_session.add(
        DispatchAssignee(
            rule_id=r.id, user_id=2, alloc_value=Decimal("5"), tier="main", is_active=True
        )
    )
    db_session.add(
        DispatchLog(ticket_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main")
    )  # 赵六已1，钱七0
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 2  # 同权重，钱七缺口大


def test_inactive_assignee_filtered(db_session: Session) -> None:
    _user(db_session, 1, "停用")
    _user(db_session, 2, "在岗")
    r = _rule(db_session)
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=False)
    )
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=2, daily_cap=20, tier="main", is_active=True)
    )
    t = _ticket(db_session)
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 2


def test_source_match_gates_rule(db_session: Session) -> None:
    """match_sources 非空时按来源精确匹配（唯一仍生效的匹配维度）。"""
    _user(db_session, 1, "运营甲")
    r = _rule(db_session, match_sources=["zammad"])
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=True)
    )
    t = _ticket(
        db_session, source_code="zammad", short_code="TKT-DISP-2", source_ticket_id="disp-2"
    )
    db_session.commit()
    res = dispatch_handler(db_session, t)
    assert res.user_id == 1


def test_writes_dispatch_log_with_ticket_id(db_session: Session) -> None:
    """add_log 落 ticket_id（不再要求 hub_issue_id）。"""
    _user(db_session, 1, "张三")
    r = _rule(db_session)
    db_session.add(
        DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=True)
    )
    t = _ticket(db_session)
    db_session.commit()
    dispatch_handler(db_session, t)
    log = db_session.query(DispatchLog).filter(DispatchLog.ticket_id == t.id).one()
    assert log.assignee_user_id == 1
    assert log.hub_issue_id is None
