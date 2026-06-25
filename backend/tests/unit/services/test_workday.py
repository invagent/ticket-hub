"""workday 计时测试 — 工作日判断 + 工作日小时累计（含节假日/调休）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Holiday
from app.services.sla.workday import BEIJING, is_workday, workday_hours_between


def test_is_workday_weekday_weekend(db_session: Session) -> None:
    assert is_workday(db_session, date(2026, 6, 25)) is True  # 周四
    assert is_workday(db_session, date(2026, 6, 27)) is False  # 周六
    assert is_workday(db_session, date(2026, 6, 28)) is False  # 周日


def test_holiday_overrides_weekday(db_session: Session) -> None:
    db_session.add(Holiday(holiday_date=date(2026, 10, 1), day_type="holiday", name="国庆"))
    db_session.commit()
    assert is_workday(db_session, date(2026, 10, 1)) is False  # 国庆虽周四也休


def test_makeup_workday_on_weekend(db_session: Session) -> None:
    db_session.add(Holiday(holiday_date=date(2026, 9, 27), day_type="workday", name="调休补班"))
    db_session.commit()
    assert is_workday(db_session, date(2026, 9, 27)) is True  # 周日补班


def _bj(y, m, d, h=0) -> datetime:  # type: ignore[no-untyped-def]
    return datetime(y, m, d, h, tzinfo=BEIJING)


def test_hours_within_one_workday(db_session: Session) -> None:
    # 周四 09:00 → 周四 13:00 = 4h
    h = workday_hours_between(db_session, _bj(2026, 6, 25, 9), _bj(2026, 6, 25, 13))
    assert abs(h - 4.0) < 1e-6


def test_hours_skip_weekend(db_session: Session) -> None:
    # 周五 22:00 → 周一 02:00：周五剩 2h + 周末 0 + 周一 2h = 4h
    h = workday_hours_between(db_session, _bj(2026, 6, 26, 22), _bj(2026, 6, 29, 2))
    assert abs(h - 4.0) < 1e-6


def test_hours_skip_holiday(db_session: Session) -> None:
    db_session.add(Holiday(holiday_date=date(2026, 10, 1), day_type="holiday"))
    db_session.commit()
    # 9-30(周三) 23:00 → 10-2(周五) 01:00：周三剩 1h + 10-1 休 0 + 10-2 1h = 2h
    h = workday_hours_between(db_session, _bj(2026, 9, 30, 23), _bj(2026, 10, 2, 1))
    assert abs(h - 2.0) < 1e-6


def test_end_before_start_zero(db_session: Session) -> None:
    assert workday_hours_between(db_session, _bj(2026, 6, 25, 13), _bj(2026, 6, 25, 9)) == 0.0


def test_naive_datetime_treated_utc(db_session: Session) -> None:
    # naive 输入按 UTC，结果非负即可（不崩）
    s = datetime(2026, 6, 25, 1)
    e = datetime(2026, 6, 25, 5)
    assert workday_hours_between(db_session, s, e) >= 0.0


def test_full_workday_24h(db_session: Session) -> None:
    # 周四 00:00 → 周五 00:00 整天工作日 = 24h
    h = workday_hours_between(
        db_session,
        datetime(2026, 6, 25, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 6, 26, tzinfo=timezone(timedelta(hours=8))),
    )
    assert abs(h - 24.0) < 1e-6
