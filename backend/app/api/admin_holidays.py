"""Admin /api/admin/holidays/* — 节假日/调休日历维护（SLA 工作日感知用）.

require_admin。

  GET    /api/admin/holidays?year=        列出（可按年）
  POST   /api/admin/holidays              批量 upsert [{date, day_type, name}]
  DELETE /api/admin/holidays/{date}       删除某日（date=YYYY-MM-DD）
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import Holiday

router = APIRouter()
logger = get_logger(__name__)


class HolidayItem(BaseModel):
    holiday_date: date
    day_type: str = Field(..., pattern="^(holiday|workday)$")
    name: str | None = None


class UpsertBody(BaseModel):
    items: list[HolidayItem] = Field(..., min_length=1, max_length=400)


class UpsertResponse(BaseModel):
    upserted: int


@router.get("", response_model=list[HolidayItem])
def list_holidays(
    year: int | None = None,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[HolidayItem]:
    stmt = select(Holiday).order_by(Holiday.holiday_date)
    if year is not None:
        stmt = stmt.where(
            Holiday.holiday_date >= date(year, 1, 1),
            Holiday.holiday_date <= date(year, 12, 31),
        )
    rows = db.execute(stmt).scalars().all()
    return [
        HolidayItem(holiday_date=r.holiday_date, day_type=r.day_type, name=r.name) for r in rows
    ]


@router.post("", response_model=UpsertResponse)
def upsert_holidays(
    body: UpsertBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UpsertResponse:
    for item in body.items:
        row = db.get(Holiday, item.holiday_date)
        if row is None:
            db.add(Holiday(holiday_date=item.holiday_date, day_type=item.day_type, name=item.name))
        else:
            row.day_type = item.day_type
            row.name = item.name
    db.commit()
    logger.info("admin_holidays_upsert", by=admin.user_id, count=len(body.items))
    return UpsertResponse(upserted=len(body.items))


@router.delete("/{holiday_date}", status_code=204)
def delete_holiday(
    holiday_date: date,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    row = db.get(Holiday, holiday_date)
    if row is None:
        raise HTTPException(status_code=404, detail="holiday not found")
    db.delete(row)
    db.commit()
