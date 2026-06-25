"""工作日计时（D4 优化 v2，移植 sample workday.py，改 sync）.

is_workday：holidays 表命中 'holiday'→休 / 'workday'→调休补班；否则按周末判断。
workday_hours_between：统计 [start, end) 之间落在工作日的小时数（非工作日不计）。
SLAWatcher 用它把超时判定从墙钟改成「工作日小时」，避免周末/长假误报。

时区：用北京时区（UTC+8）判断「日」边界（节假日按自然日，不能用 UTC 切日）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Holiday

BEIJING = timezone(timedelta(hours=8))


def is_workday(db: Session, d: date) -> bool:
    """该日是否工作日：holidays 表优先（holiday=休/workday=补班），否则周一~周五。"""
    row = db.execute(select(Holiday).where(Holiday.holiday_date == d)).scalar_one_or_none()
    if row is not None:
        return row.day_type == "workday"
    return d.weekday() < 5  # 0=周一 … 4=周五


def workday_hours_between(db: Session, start: datetime, end: datetime) -> float:
    """[start, end) 之间落在工作日的小时数。end<=start 返回 0。

    逐日推进（按北京时区切日），整天工作日计 24h，部分天按落在该日的秒数计；
    非工作日不计。holidays 命中走 is_workday。
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    start = start.astimezone(BEIJING)
    end = end.astimezone(BEIJING)
    if end <= start:
        return 0.0

    total = 0.0
    cur = start
    guard = 0
    while cur < end and guard < 4000:
        guard += 1
        next_midnight = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seg_end = min(next_midnight, end)
        if is_workday(db, cur.date()):
            total += (seg_end - cur).total_seconds() / 3600.0
        cur = seg_end
    return total
