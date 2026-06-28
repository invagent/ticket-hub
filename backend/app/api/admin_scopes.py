"""Admin /api/admin/scopes/* endpoints — routing scope CRUD.

  GET    /api/admin/scopes/modules                  list module scopes
  POST   /api/admin/scopes/modules                  add a module scope
  DELETE /api/admin/scopes/modules/{id}             remove a module scope
  GET    /api/admin/scopes/features                 list feature scopes
  POST   /api/admin/scopes/features                 add a feature scope
  DELETE /api/admin/scopes/features/{id}            remove a feature scope
  GET    /api/admin/scopes/history                  list change audit (50 rows by default)

All endpoints require role='admin' (per upgrade_plan §4.12 — supervisors
can only relink, not change scope ownership).

Each add/delete writes one assignment_scope_history row in the same transaction.
Duplicate (user_id, product_line_code, module) returns 409 Conflict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.repositories.assignment_scope import AssignmentScopeAdminRepository

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs ----------------------------------------------------------------


class ModuleScopeOut(BaseModel):
    id: int
    user_id: int
    product_line_code: str
    module: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FeatureScopeOut(BaseModel):
    id: int
    user_id: int
    feature: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ModuleScopeIn(BaseModel):
    user_id: int = Field(..., gt=0)
    product_line_code: str = Field(..., min_length=1, max_length=64)
    module: str = Field(..., min_length=1, max_length=128)


class FeatureScopeIn(BaseModel):
    user_id: int = Field(..., gt=0)
    feature: str = Field(..., min_length=1, max_length=128)


class HistoryOut(BaseModel):
    id: int
    scope_type: str
    user_id: int
    action: str
    payload: dict[str, Any]
    changed_by: int
    changed_at: datetime

    model_config = {"from_attributes": True}


# ---- modules -------------------------------------------------------------


@router.get("/modules", response_model=list[ModuleScopeOut])
def list_modules(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
    user_id: int | None = Query(None),
    product_line_code: str | None = Query(None),
    module: str | None = Query(None),
) -> list[ModuleScopeOut]:
    rows = AssignmentScopeAdminRepository(db).list_modules(
        user_id=user_id, product_line_code=product_line_code, module=module
    )
    return [ModuleScopeOut.model_validate(r) for r in rows]


@router.post("/modules", response_model=ModuleScopeOut, status_code=201)
def add_module(
    body: ModuleScopeIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ModuleScopeOut:
    repo = AssignmentScopeAdminRepository(db)
    try:
        row = repo.add_module(
            user_id=body.user_id,
            product_line_code=body.product_line_code,
            module=body.module,
            changed_by=admin.user_id,
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # UNIQUE (product_line_code, module, user_id) violated
        raise HTTPException(
            status_code=409,
            detail=(
                f"scope already exists: "
                f"user_id={body.user_id} product_line_code={body.product_line_code} module={body.module}"
            ),
        ) from e
    db.refresh(row)
    logger.info("admin_scope_module_added", scope_id=row.id, by=admin.user_id)
    return ModuleScopeOut.model_validate(row)


@router.delete("/modules/{scope_id}", status_code=204)
def delete_module(
    scope_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    repo = AssignmentScopeAdminRepository(db)
    deleted = repo.delete_module(scope_id=scope_id, changed_by=admin.user_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="module scope not found")
    db.commit()
    logger.info("admin_scope_module_deleted", scope_id=scope_id, by=admin.user_id)
    return Response(status_code=204)


# ---- features ------------------------------------------------------------


@router.get("/features", response_model=list[FeatureScopeOut])
def list_features(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
    user_id: int | None = Query(None),
    feature: str | None = Query(None),
) -> list[FeatureScopeOut]:
    rows = AssignmentScopeAdminRepository(db).list_features(user_id=user_id, feature=feature)
    return [FeatureScopeOut.model_validate(r) for r in rows]


@router.post("/features", response_model=FeatureScopeOut, status_code=201)
def add_feature(
    body: FeatureScopeIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> FeatureScopeOut:
    repo = AssignmentScopeAdminRepository(db)
    try:
        row = repo.add_feature(user_id=body.user_id, feature=body.feature, changed_by=admin.user_id)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"scope already exists: user_id={body.user_id} feature={body.feature}",
        ) from e
    db.refresh(row)
    logger.info("admin_scope_feature_added", scope_id=row.id, by=admin.user_id)
    return FeatureScopeOut.model_validate(row)


@router.delete("/features/{scope_id}", status_code=204)
def delete_feature(
    scope_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    repo = AssignmentScopeAdminRepository(db)
    deleted = repo.delete_feature(scope_id=scope_id, changed_by=admin.user_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="feature scope not found")
    db.commit()
    logger.info("admin_scope_feature_deleted", scope_id=scope_id, by=admin.user_id)
    return Response(status_code=204)


# ---- history audit -------------------------------------------------------


@router.get("/history", response_model=list[HistoryOut])
def list_history(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
    user_id: int | None = Query(None),
    scope_type: str | None = Query(None, pattern="^(module|feature)$"),
    limit: int = Query(50, ge=1, le=500),
) -> list[HistoryOut]:
    rows = AssignmentScopeAdminRepository(db).list_history(
        user_id=user_id, scope_type=scope_type, limit=limit
    )
    return [HistoryOut.model_validate(r) for r in rows]
