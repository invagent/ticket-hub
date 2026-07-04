"""工作台看板指标 tests — 范围切界 / 漏斗子集 / SLO 环比 / 来源分布."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Source, Ticket, User
from app.services.metrics.workbench import compute_workbench_metrics, range_window
from app.services.sla.workday import BEIJING

# 固定「现在」：北京时间 2026-07-15（周三）12:00
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=BEIJING).astimezone(UTC)


def test_range_window_today() -> None:
    start, prev = range_window("today", now=NOW)
    assert start.astimezone(BEIJING).hour == 0
    assert start.astimezone(BEIJING).day == 15
    assert prev.astimezone(BEIJING).day == 14


def test_range_window_week_starts_monday() -> None:
    start, prev = range_window("week", now=NOW)
    assert start.astimezone(BEIJING).weekday() == 0  # 周一
    assert start.astimezone(BEIJING).day == 13
    assert prev.astimezone(BEIJING).day == 6


def test_range_window_month() -> None:
    start, prev = range_window("month", now=NOW)
    assert start.astimezone(BEIJING).day == 1
    assert start.astimezone(BEIJING).month == 7
    assert prev.astimezone(BEIJING).month == 6


def test_range_window_invalid() -> None:
    with pytest.raises(ValueError, match="invalid range"):
        range_window("year", now=NOW)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all([Source(code="ksm", name="KSM"), Source(code="ai_cs", name="AI 客服")])
    db_session.add(User(id=1, feishu_uid="ou_a", name="a", role="assignee"))
    db_session.commit()

    def mk(i: int, *, created: datetime, **kw) -> Ticket:  # type: ignore[no-untyped-def]
        return Ticket(
            id=i,
            short_code=f"TKT-{i}",
            source_code=kw.pop("source_code", "ksm"),
            source_ticket_id=f"s-{i}",
            type="Raw",
            status=kw.pop("status", "received"),
            title=f"t{i}",
            created_at=created,
            **kw,
        )

    today = NOW - timedelta(hours=1)  # 本期内
    yesterday = NOW - timedelta(days=1)  # 上期（today 范围）
    db_session.add_all(
        [
            # 今天 3 单：1 全新收到 / 1 已分类已分配处理中 / 1 已解决
            mk(1, created=today),
            mk(
                2,
                created=today,
                predicted_type="Bug_fix",
                assigned_user_id=1,
                status="in_progress",
            ),
            mk(3, created=today, predicted_type="Operation", status="done", source_code="ai_cs"),
            # 昨天 2 单：全部已分配（上期命中率 100%）
            mk(4, created=yesterday, assigned_user_id=1),
            mk(5, created=yesterday, assigned_user_id=1),
        ]
    )
    db_session.commit()
    return db_session


def test_funnel_counts_subsets(world: Session) -> None:
    m = compute_workbench_metrics(world, "today", now=NOW)
    assert m.funnel.received == 3
    assert m.funnel.classified == 2
    assert m.funnel.assigned == 1
    assert m.funnel.in_progress == 1
    assert m.funnel.resolved == 1


def test_slo_delta_vs_previous_window(world: Session) -> None:
    m = compute_workbench_metrics(world, "today", now=NOW)
    auto = next(s for s in m.slo if s.key == "auto_hit")
    # 本期 1/3=33.3%，上期 2/2=100% → delta -66.7pt，向好=False
    assert auto.value == pytest.approx(0.3333, abs=1e-3)
    assert auto.delta_pt == pytest.approx(-66.7, abs=0.1)
    assert auto.good is False


def test_slo_delta_none_when_prev_empty(world: Session) -> None:
    # week 范围：上周没有任何工单 → delta None
    m = compute_workbench_metrics(world, "week", now=NOW)
    auto = next(s for s in m.slo if s.key == "auto_hit")
    assert auto.delta_pt is None


def test_sources_distribution(world: Session) -> None:
    m = compute_workbench_metrics(world, "today", now=NOW)
    assert m.sources == {"ksm": 2, "ai_cs": 1}
