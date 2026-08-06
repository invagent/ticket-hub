"""DispatchRepository：匹配、今日计数、兜底配置。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import DispatchLog, DispatchRule
from app.repositories.dispatch import DispatchRepository


def _rule(db: Session, **ov) -> DispatchRule:
    base = {
        "name": "r", "match_sources": [], "match_product_lines": [], "match_modules": [],
        "match_sla": [], "dispatch_mode": "count", "rule_type": "primary", "priority": 100,
        "is_active": True,
    }
    base.update(ov)
    r = DispatchRule(**base)
    db.add(r)
    db.flush()
    return r


def test_match_and_or_semantics(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="A", match_product_lines=["cloud-fapiao"], match_modules=["数电开票"], priority=10)
    _rule(db_session, name="B", match_product_lines=["cloud-erp-star"], priority=20)  # 不同产品线
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", product_line_code="cloud-fapiao", module="数电开票", sla=None)
    assert [r.name for r in hits] == ["A"]  # 只 A 命中，B 产品线不符


def test_empty_list_means_unrestricted(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="ALL")  # 全空 = 不限
    db_session.commit()
    hits = repo.find_matching_rules(source="zhichi", product_line_code="x", module="y", sla=None)
    assert [r.name for r in hits] == ["ALL"]


def test_priority_order(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="LO", priority=50)
    _rule(db_session, name="HI", priority=5)
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", product_line_code="p", module="m", sla=None)
    assert [r.name for r in hits] == ["HI", "LO"]  # priority 升序


def test_today_counts_windows_by_day(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    r = _rule(db_session)
    now = datetime.now(UTC)
    db_session.add(DispatchLog(hub_issue_id=1, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=now))
    yesterday = now - timedelta(days=1)
    db_session.add(DispatchLog(hub_issue_id=2, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=yesterday))
    db_session.commit()
    counts = repo.today_counts(r.id)
    assert counts.get(7) == 1  # 昨天那条不计


def test_inactive_rule_excluded(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="OFF", is_active=False)
    db_session.commit()
    assert repo.find_matching_rules(source="ksm", product_line_code="p", module="m", sla=None) == []
