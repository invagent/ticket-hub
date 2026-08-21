"""Admin /api/admin/dispatch/* endpoints — 运营分派规则 CRUD.

  GET    /api/admin/dispatch/rules                       list rules (priority asc)
  POST   /api/admin/dispatch/rules                       create a rule
  PUT    /api/admin/dispatch/rules/{id}                   update a rule
  DELETE /api/admin/dispatch/rules/{id}                   delete a rule
  GET    /api/admin/dispatch/rules/{id}/assignees         list assignees under a rule
  POST   /api/admin/dispatch/rules/{id}/assignees         add an assignee to a rule
  DELETE /api/admin/dispatch/rules/{id}/assignees/{aid}   remove an assignee
  GET    /api/admin/dispatch/config                       list all config key/value
  PUT    /api/admin/dispatch/config                       upsert one config key/value
  GET    /api/admin/dispatch/logs                         list dispatch log (optionally by rule_id)
  GET    /api/admin/dispatch/sla-levels                   list SLA level code/name mapping

All endpoints require role='admin'. This is orthogonal to routing 研发责任田
(assignment_scopes_*) — dispatch is for Operation 运营分派引擎 only.
0032: rule_code (FPYRULE####) + updated_by + SlaLevel seed table.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, SlaLevel

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs ------------------------------------------------------------------


class RuleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    match_sources: list[str] = Field(default_factory=list)
    match_product_lines: list[str] = Field(default_factory=list)
    match_modules: list[str] = Field(default_factory=list)
    match_sla: list[str] = Field(default_factory=list)
    dispatch_mode: str = Field(pattern="^(count|ratio)$")
    rule_type: str = Field(default="primary", pattern="^(primary|overflow)$")
    overflow_rule_id: int | None = None
    priority: int = 100
    is_active: bool = True


class RuleOut(RuleBody):
    id: int
    rule_code: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    created_at: str | None = None


class AssigneeBody(BaseModel):
    user_id: int = Field(..., gt=0)
    alloc_value: float = 1
    daily_cap: int | None = None
    tier: str = Field(default="main", pattern="^(main|overflow)$")
    is_active: bool = True


class AssigneeOut(AssigneeBody):
    id: int
    rule_id: int


class ConfigBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., max_length=128)


class LogOut(BaseModel):
    id: int
    hub_issue_id: int
    rule_id: int | None
    assignee_user_id: int
    tier_hit: str
    created_at: str


class SlaLevelOut(BaseModel):
    code: str
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


def _generate_rule_code(db: Session) -> str:
    """Generate next FPYRULE code: FPYRULE0001, FPYRULE0002, ..."""
    max_id = db.execute(select(func.max(DispatchRule.id))).scalar() or 0
    return f"FPYRULE{(max_id + 1):04d}"


def _rule_out(r: DispatchRule) -> RuleOut:
    return RuleOut(
        id=r.id,
        rule_code=r.rule_code,
        name=r.name,
        match_sources=r.match_sources,
        match_product_lines=r.match_product_lines,
        match_modules=r.match_modules,
        match_sla=r.match_sla,
        dispatch_mode=r.dispatch_mode,
        rule_type=r.rule_type,
        overflow_rule_id=r.overflow_rule_id,
        priority=r.priority,
        is_active=r.is_active,
        updated_by=r.updated_by,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


def _assignee_out(a: DispatchAssignee) -> AssigneeOut:
    return AssigneeOut(
        id=a.id,
        rule_id=a.rule_id,
        user_id=a.user_id,
        alloc_value=float(a.alloc_value),
        daily_cap=a.daily_cap,
        tier=a.tier,
        is_active=a.is_active,
    )


# ---- rules ------------------------------------------------------------------


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[RuleOut]:
    rows = (
        db.execute(select(DispatchRule).order_by(DispatchRule.priority.asc(), DispatchRule.id.asc()))
        .scalars()
        .all()
    )
    return [_rule_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut)
def create_rule(
    body: RuleBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> RuleOut:
    code = _generate_rule_code(db)
    r = DispatchRule(**body.model_dump(), rule_code=code, updated_by=admin.name or str(admin.user_id))
    db.add(r)
    db.commit()
    db.refresh(r)
    logger.info("admin_dispatch_rule_created", rule_id=r.id, rule_code=code, by=admin.user_id)
    return _rule_out(r)


@router.put("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: int,
    body: RuleBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> RuleOut:
    r = db.get(DispatchRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    r.updated_by = admin.name or str(admin.user_id)
    db.commit()
    db.refresh(r)
    logger.info("admin_dispatch_rule_updated", rule_id=r.id, by=admin.user_id)
    return _rule_out(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    r = db.get(DispatchRule, rule_id)
    if r is not None:
        db.delete(r)
        db.commit()
        logger.info("admin_dispatch_rule_deleted", rule_id=rule_id, by=admin.user_id)
    return Response(status_code=204)


# ---- assignees ---------------------------------------------------------------


@router.get("/rules/{rule_id}/assignees", response_model=list[AssigneeOut])
def list_assignees(
    rule_id: int,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AssigneeOut]:
    if db.get(DispatchRule, rule_id) is None:
        raise HTTPException(status_code=404, detail="rule not found")
    rows = (
        db.execute(
            select(DispatchAssignee).where(DispatchAssignee.rule_id == rule_id).order_by(DispatchAssignee.id)
        )
        .scalars()
        .all()
    )
    return [_assignee_out(a) for a in rows]


@router.post("/rules/{rule_id}/assignees", response_model=AssigneeOut)
def add_assignee(
    rule_id: int,
    body: AssigneeBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AssigneeOut:
    if db.get(DispatchRule, rule_id) is None:
        raise HTTPException(status_code=404, detail="rule not found")
    a = DispatchAssignee(
        rule_id=rule_id,
        user_id=body.user_id,
        alloc_value=Decimal(str(body.alloc_value)),
        daily_cap=body.daily_cap,
        tier=body.tier,
        is_active=body.is_active,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    logger.info("admin_dispatch_assignee_added", rule_id=rule_id, assignee_id=a.id, by=admin.user_id)
    return _assignee_out(a)


@router.delete("/rules/{rule_id}/assignees/{assignee_id}", status_code=204)
def delete_assignee(
    rule_id: int,
    assignee_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    a = db.get(DispatchAssignee, assignee_id)
    if a is not None and a.rule_id == rule_id:
        db.delete(a)
        db.commit()
        logger.info("admin_dispatch_assignee_deleted", rule_id=rule_id, assignee_id=assignee_id, by=admin.user_id)
    return Response(status_code=204)


# ---- config -------------------------------------------------------------------


@router.get("/config")
def get_config(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    rows = db.execute(select(DispatchConfig)).scalars().all()
    return {r.key: r.value for r in rows}


@router.put("/config")
def put_config(
    body: ConfigBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    row = db.get(DispatchConfig, body.key)
    if row is None:
        db.add(DispatchConfig(key=body.key, value=body.value))
    else:
        row.value = body.value
    db.commit()
    logger.info("admin_dispatch_config_upserted", key=body.key, by=admin.user_id)
    return {body.key: body.value}


# ---- sla levels ---------------------------------------------------------------


@router.get("/sla-levels", response_model=list[SlaLevelOut])
def list_sla_levels(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[SlaLevelOut]:
    rows = db.execute(select(SlaLevel).order_by(SlaLevel.sort_order)).scalars().all()
    return [SlaLevelOut.model_validate(r) for r in rows]


# ---- logs ---------------------------------------------------------------------


@router.get("/logs", response_model=list[LogOut])
def list_logs(
    rule_id: int | None = Query(default=None),
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(DispatchLog).order_by(DispatchLog.created_at.desc()).limit(200)
    if rule_id is not None:
        stmt = stmt.where(DispatchLog.rule_id == rule_id)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "hub_issue_id": r.hub_issue_id,
            "rule_id": r.rule_id,
            "assignee_user_id": r.assignee_user_id,
            "tier_hit": r.tier_hit,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
