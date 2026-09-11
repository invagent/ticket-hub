"""Admin /api/admin/sla-levels endpoints — 服务等级 & SLA 配置管理.

  GET    /api/admin/sla-levels       list all SLA level configs
  POST   /api/admin/sla-levels       create an SLA level config (auto assigns SEVERLEVEL#### id)
  PUT    /api/admin/sla-levels/{id}  update an SLA level config by id

All endpoints require role='admin'.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import SlaLevel

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs ------------------------------------------------------------------


class SlaLevelDetailOut(BaseModel):
    id: str
    code: str
    name: str
    sort_order: int
    issue_levels: str
    issue_types: str
    sla_hours: float
    source_system: str
    source_system_field: str
    source_system_code: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None

    model_config = {"from_attributes": True}


class SlaLevelCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="服务等级名称")
    issue_levels: str = Field(..., min_length=1, description="问题级别，顿号隔开")
    issue_types: str = Field(..., min_length=1, description="问题类型，顿号隔开")
    sla_hours: float = Field(..., gt=0, description="标准处理时长SLA，必须为大于0的正数")
    source_system: str = Field(..., min_length=1, max_length=64, description="来源系统")
    source_system_field: str = Field(..., min_length=1, max_length=64, description="来源系统字段")
    source_system_code: str = Field(..., min_length=1, max_length=64, description="来源系统code")
    sort_order: int = 0


class SlaLevelUpdateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="服务等级名称")
    issue_levels: str = Field(..., min_length=1, description="问题级别，顿号隔开")
    issue_types: str = Field(..., min_length=1, description="问题类型，顿号隔开")
    sla_hours: float = Field(..., gt=0, description="标准处理时长SLA，必须为大于0的正数")
    source_system: str = Field(..., min_length=1, max_length=64, description="来源系统")
    source_system_field: str = Field(..., min_length=1, max_length=64, description="来源系统字段")
    source_system_code: str = Field(..., min_length=1, max_length=64, description="来源系统code")
    sort_order: int | None = None


# ---- Helpers ---------------------------------------------------------------


def _generate_sla_level_id(db: Session) -> str:
    """Generate next SEVERLEVEL code: SEVERLEVEL0001, SEVERLEVEL0002, ..."""
    rows = db.execute(select(SlaLevel.id).where(SlaLevel.id.like("SEVERLEVEL%"))).scalars().all()
    max_num = 0
    for r in rows:
        suffix = r[len("SEVERLEVEL") :]
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))
    return f"SEVERLEVEL{(max_num + 1):04d}"


def _to_out(r: SlaLevel) -> SlaLevelDetailOut:
    return SlaLevelDetailOut(
        id=r.id,
        code=r.code,
        name=r.name,
        sort_order=r.sort_order,
        issue_levels=r.issue_levels,
        issue_types=r.issue_types,
        sla_hours=float(r.sla_hours),
        source_system=r.source_system,
        source_system_field=r.source_system_field,
        source_system_code=r.source_system_code or r.code,
        updated_by=r.updated_by,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
    )


# ---- Endpoints -------------------------------------------------------------


@router.get("", response_model=list[SlaLevelDetailOut])
def list_sla_levels(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[SlaLevelDetailOut]:
    """List all SLA level configurations sorted by sort_order and id."""
    rows = db.execute(select(SlaLevel).order_by(SlaLevel.sort_order.asc(), SlaLevel.id.asc())).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=SlaLevelDetailOut, status_code=status.HTTP_201_CREATED)
def create_sla_level(
    body: SlaLevelCreateBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> SlaLevelDetailOut:
    """Create a new SLA level configuration. Automatically generates SEVERLEVEL#### primary key."""
    new_id = _generate_sla_level_id(db)
    now = datetime.now(timezone.utc)
    user_name = admin.name or f"User-{admin.user_id}"

    level = SlaLevel(
        id=new_id,
        code=body.source_system_code.strip(),
        name=body.name.strip(),
        sort_order=body.sort_order,
        issue_levels=body.issue_levels.strip(),
        issue_types=body.issue_types.strip(),
        sla_hours=body.sla_hours,
        source_system=body.source_system.strip(),
        source_system_field=body.source_system_field.strip(),
        source_system_code=body.source_system_code.strip(),
        updated_by=user_name,
        updated_at=now,
    )
    db.add(level)
    db.commit()
    db.refresh(level)
    logger.info("sla_level_created", id=new_id, name=level.name, updated_by=user_name)
    return _to_out(level)


@router.put("/{level_id}", response_model=SlaLevelDetailOut)
def update_sla_level(
    level_id: str,
    body: SlaLevelUpdateBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> SlaLevelDetailOut:
    """Update an existing SLA level configuration."""
    level = db.get(SlaLevel, level_id)
    if not level:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"SLA level {level_id} not found")

    now = datetime.now(timezone.utc)
    user_name = admin.name or f"User-{admin.user_id}"

    level.name = body.name.strip()
    level.issue_levels = body.issue_levels.strip()
    level.issue_types = body.issue_types.strip()
    level.sla_hours = body.sla_hours
    level.source_system = body.source_system.strip()
    level.source_system_field = body.source_system_field.strip()
    level.source_system_code = body.source_system_code.strip()
    level.code = body.source_system_code.strip()
    if body.sort_order is not None:
        level.sort_order = body.sort_order
    level.updated_by = user_name
    level.updated_at = now

    db.commit()
    db.refresh(level)
    logger.info("sla_level_updated", id=level_id, name=level.name, updated_by=user_name)
    return _to_out(level)
