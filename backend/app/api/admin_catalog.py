"""Admin catalog endpoints — modules / features.

  GET    /api/admin/modules?product_line_code=...    list (filter optional)
  POST   /api/admin/modules                           add
  PATCH  /api/admin/modules/{id}                      update status/owners
  DELETE /api/admin/modules/{id}                      hard delete

  GET    /api/admin/features
  POST   /api/admin/features
  DELETE /api/admin/features/{id}

All admin only. UNIQUE-violation → 409.
0031: module gains status/product_owner/dev_owners/updated_by fields.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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
    product_line_name: str | None = None
    product_line_category: str | None = None
    name: str
    is_active: bool
    status: str = "enabled"
    product_owner: str | None = None
    dev_owners: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ModuleIn(BaseModel):
    product_line_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    product_owner: str | None = None
    dev_owners: str | None = None


class ModulePatch(BaseModel):
    status: str | None = None          # "enabled" | "disabled"
    product_owner: str | None = None
    dev_owners: str | None = None
    updated_by: str | None = None


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

    # batch load product line names + categories
    codes = list({r.product_line_code for r in rows})
    pl_map: dict[str, ProductLine] = {}
    if codes:
        pls = db.execute(select(ProductLine).where(ProductLine.code.in_(codes))).scalars().all()
        pl_map = {pl.code: pl for pl in pls}

    result = []
    for r in rows:
        pl = pl_map.get(r.product_line_code)
        result.append(ModuleOut(
            id=r.id,
            product_line_code=r.product_line_code,
            product_line_name=pl.name if pl else None,
            product_line_category=pl.category if pl else None,
            name=r.name,
            is_active=r.is_active,
            status=getattr(r, "status", "enabled") or "enabled",
            product_owner=getattr(r, "product_owner", None),
            dev_owners=getattr(r, "dev_owners", None),
            updated_by=getattr(r, "updated_by", None),
            created_at=r.created_at,
            updated_at=getattr(r, "updated_at", None),
        ))
    return result


@router.post("/modules", response_model=ModuleOut, status_code=201)
def add_module(
    body: ModuleIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ModuleOut:
    pl = db.execute(
        select(ProductLine).where(ProductLine.code == body.product_line_code)
    ).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404, detail="product_line not found")

    row = Module(
        product_line_code=body.product_line_code,
        name=body.name,
        is_active=True,
        status="enabled",
        product_owner=body.product_owner,
        dev_owners=body.dev_owners,
        updated_by=None,
    )
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
        "admin_module_added",
        id=row.id,
        by=admin.user_id,
        product_line_code=body.product_line_code,
        name=body.name,
    )
    return ModuleOut(
        id=row.id,
        product_line_code=row.product_line_code,
        product_line_name=pl.name,
        product_line_category=pl.category,
        name=row.name,
        is_active=row.is_active,
        status=row.status,
        product_owner=row.product_owner,
        dev_owners=row.dev_owners,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.patch("/modules/{module_id}", response_model=ModuleOut)
def patch_module(
    module_id: int,
    body: ModulePatch,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ModuleOut:
    row = db.get(Module, module_id)
    if row is None:
        raise HTTPException(status_code=404, detail="module not found")

    patch = body.model_dump(exclude_unset=True)
    if "status" in patch and patch["status"] not in ("enabled", "disabled"):
        raise HTTPException(status_code=422, detail="status must be 'enabled' or 'disabled'")

    for field, value in patch.items():
        setattr(row, field, value)
    # sync is_active with status
    if "status" in patch:
        row.is_active = patch["status"] == "enabled"

    db.commit()
    db.refresh(row)

    pl = db.execute(
        select(ProductLine).where(ProductLine.code == row.product_line_code)
    ).scalar_one_or_none()

    logger.info("admin_module_patched", id=module_id, by=admin.user_id, fields=list(patch.keys()))
    return ModuleOut(
        id=row.id,
        product_line_code=row.product_line_code,
        product_line_name=pl.name if pl else None,
        product_line_category=pl.category if pl else None,
        name=row.name,
        is_active=row.is_active,
        status=row.status,
        product_owner=row.product_owner,
        dev_owners=row.dev_owners,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete("/modules/{module_id}", status_code=204)
def delete_module(
    module_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    row = db.get(Module, module_id)
    if row is None:
        raise HTTPException(status_code=404, detail="module not found")
    db.delete(row)
    db.commit()
    logger.info("admin_module_deleted", id=module_id, by=admin.user_id)
    return Response(status_code=204)


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
        raise HTTPException(status_code=409, detail=f"feature already exists: {body.name}") from e
    db.refresh(row)
    logger.info("admin_feature_added", id=row.id, by=admin.user_id, name=body.name)
    return FeatureOut.model_validate(row)


@router.delete("/features/{feature_id}", status_code=204)
def delete_feature(
    feature_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    row = db.get(Feature, feature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feature not found")
    db.delete(row)
    db.commit()
    logger.info("admin_feature_deleted", id=feature_id, by=admin.user_id)
    return Response(status_code=204)
