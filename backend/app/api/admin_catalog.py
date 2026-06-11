"""Admin catalog endpoints — modules / features.

  GET    /api/admin/modules?product_line_code=...    list (filter optional)
  POST   /api/admin/modules                           add
  DELETE /api/admin/modules/{id}                      hard delete

  GET    /api/admin/features
  POST   /api/admin/features
  DELETE /api/admin/features/{id}

All admin only. UNIQUE-violation → 409.

Note on delete semantics:
- Hard delete (no soft flag) keeps the table tight; if a deleted module is
  still referenced by an assignment_scope row, the FK on assignment_scopes
  doesn't apply (we kept assignment_scopes.module as a string for migration
  flexibility). Admin should clean up scopes first.
- For "deactivate without removing", use PATCH with is_active (not yet
  exposed; add when needed).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import Feature, Module, ProductLine

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs --------------------------------------------------------------


class ModuleOut(BaseModel):
    id: int
    product_line_code: str
    name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ModuleIn(BaseModel):
    product_line_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)


class FeatureOut(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class FeatureIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


# ---- modules ------------------------------------------------------------


@router.get("/modules", response_model=list[ModuleOut])
def list_modules(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
    product_line_code: str | None = Query(None),
    active_only: bool = Query(True),
) -> list[ModuleOut]:
    stmt = select(Module)
    if product_line_code:
        stmt = stmt.where(Module.product_line_code == product_line_code)
    if active_only:
        stmt = stmt.where(Module.is_active.is_(True))
    stmt = stmt.order_by(Module.product_line_code, Module.name)
    rows = db.execute(stmt).scalars().all()
    return [ModuleOut.model_validate(r) for r in rows]


@router.post("/modules", response_model=ModuleOut, status_code=201)
def add_module(
    body: ModuleIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ModuleOut:
    # FK check (better error than the bare IntegrityError from Postgres)
    pl = db.execute(
        select(ProductLine).where(ProductLine.code == body.product_line_code)
    ).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404, detail="product_line not found")

    row = Module(product_line_code=body.product_line_code, name=body.name, is_active=True)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"module already exists: ({body.product_line_code}, {body.name})",
        ) from e
    db.refresh(row)
    logger.info(
        "admin_module_added", id=row.id, by=admin.user_id,
        product_line_code=body.product_line_code, name=body.name,
    )
    return ModuleOut.model_validate(row)


@router.delete("/modules/{module_id}", status_code=204)
def delete_module(
    module_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    row = db.get(Module, module_id)
    if row is None:
        raise HTTPException(status_code=404, detail="module not found")
    db.delete(row)
    db.commit()
    logger.info("admin_module_deleted", id=module_id, by=admin.user_id)


# ---- features -----------------------------------------------------------


@router.get("/features", response_model=list[FeatureOut])
def list_features(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
    active_only: bool = Query(True),
) -> list[FeatureOut]:
    stmt = select(Feature)
    if active_only:
        stmt = stmt.where(Feature.is_active.is_(True))
    stmt = stmt.order_by(Feature.name)
    rows = db.execute(stmt).scalars().all()
    return [FeatureOut.model_validate(r) for r in rows]


@router.post("/features", response_model=FeatureOut, status_code=201)
def add_feature(
    body: FeatureIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> FeatureOut:
    row = Feature(name=body.name, is_active=True)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"feature already exists: {body.name}"
        ) from e
    db.refresh(row)
    logger.info("admin_feature_added", id=row.id, by=admin.user_id, name=body.name)
    return FeatureOut.model_validate(row)


@router.delete("/features/{feature_id}", status_code=204)
def delete_feature(
    feature_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    row = db.get(Feature, feature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feature not found")
    db.delete(row)
    db.commit()
    logger.info("admin_feature_deleted", id=feature_id, by=admin.user_id)
