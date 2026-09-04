"""DispatchRepository：匹配（仅来源+SLA，match_product_lines/modules 已暂停使用）、
今日计数、兜底配置。"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import DispatchLog, DispatchRule
from app.repositories.dispatch import DispatchRepository
from app.services.sla.workday import BEIJING


def _rule(db: Session, **ov) -> DispatchRule:
    base = {
        "name": "r",
        "match_sources": [],
        "match_product_lines": [],
        "match_modules": [],
        "match_sla": [],
        "dispatch_mode": "count",
        "rule_type": "primary",
        "priority": 100,
        "is_active": True,
    }
    base.update(ov)
    r = DispatchRule(**base)
    db.add(r)
    db.flush()
    return r


def test_match_source_and_or_semantics(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="A", match_sources=["ksm"], priority=10)
    _rule(db_session, name="B", match_sources=["zammad"], priority=20)  # 不同来源
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", sla=None)
    assert [r.name for r in hits] == ["A"]  # 只 A 命中，B 来源不符


def test_match_product_lines_no_longer_used(db_session: Session) -> None:
    """match_product_lines/match_modules 字段仍存在但已不参与匹配——配了跟入参
    不符的值也不影响命中，只看 match_sources/match_sla。"""
    repo = DispatchRepository(db_session)
    _rule(db_session, name="A", match_product_lines=["cloud-fapiao"], match_modules=["数电开票"])
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", sla=None)
    assert [r.name for r in hits] == ["A"]


def test_empty_list_means_unrestricted(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="ALL")  # 全空 = 不限
    db_session.commit()
    hits = repo.find_matching_rules(source="zhichi", sla=None)
    assert [r.name for r in hits] == ["ALL"]


def test_priority_order(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="LO", priority=50)
    _rule(db_session, name="HI", priority=5)
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", sla=None)
    assert [r.name for r in hits] == ["HI", "LO"]  # priority 升序


def test_today_counts_windows_by_day(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    r = _rule(db_session)
    now = datetime.now(UTC)
    db_session.add(
        DispatchLog(ticket_id=1, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=now)
    )
    yesterday = now - timedelta(days=1)
    db_session.add(
        DispatchLog(
            ticket_id=2, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=yesterday
        )
    )
    db_session.commit()
    counts = repo.today_counts(r.id)
    assert counts.get(7) == 1  # 昨天那条不计


def test_inactive_rule_excluded(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="OFF", is_active=False)
    db_session.commit()
    assert repo.find_matching_rules(source="ksm", sla=None) == []


def test_today_counts_uses_beijing_day_boundary(db_session: Session) -> None:
    """北京日边界：北京时间今天凌晨 2:00（= UTC 昨天 18:00）应算进今天计数。

    UTC-day 实现会把它误判为昨天（漏算）；北京-day 实现正确算入（count=1）。
    """
    repo = DispatchRepository(db_session)
    r = _rule(db_session)
    today_bj = datetime.now(UTC).astimezone(BEIJING).date()
    at_bj_2am = datetime.combine(today_bj, time(2, 0), tzinfo=BEIJING)
    created_utc = at_bj_2am.astimezone(UTC)  # 北京今天 02:00 -> UTC 昨天 18:00
    db_session.add(
        DispatchLog(
            ticket_id=1, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=created_utc
        )
    )
    db_session.commit()
    counts = repo.today_counts(r.id)
    assert counts.get(7) == 1  # 北京日历它是今天，必须算入
