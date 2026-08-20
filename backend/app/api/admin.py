"""Admin endpoints (sources / product_lines).

D0: read-only listing for sources + product_lines.
D1: users moved to admin_users.py (full CRUD).
D2: scopes in admin_scopes.py (full CRUD).
D2-C: per-product-line SLA threshold PATCH.
D2-G2: product_lines POST + DELETE so admin can add/remove product_lines
       directly from the catalog UI alongside modules.
0031: category field + auto-generated PROLINE code + module_count in listing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import Module, ProductLine, Source, Ticket

router = APIRouter()
logger = get_logger(__name__)

CATEGORY_OPTIONS = ["开票", "收票", "影像", "基础", "EOP", "档案"]


class SourceOut(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class ProductLineOut(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool
    category: str | None = None
    sla_reply_hours: int | None = None
    sla_resolve_hours: int | None = None
    created_at: str | None = None
    module_count: int = 0

    model_config = {"from_attributes": True}


class ProductLinePatch(BaseModel):
    """PATCH body for /api/admin/product-lines/{code}."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = None
    sla_reply_hours: int | None = Field(default=None, ge=1, le=168)
    sla_resolve_hours: int | None = Field(default=None, ge=1, le=168)
    is_active: bool | None = None


class ProductLineIn(BaseModel):
    """POST body for /api/admin/product-lines. code is auto-generated."""

    name: str = Field(..., min_length=1, max_length=128)
    category: str = Field(..., min_length=1, max_length=64)
    sla_reply_hours: int | None = Field(default=None, ge=1, le=168)
    sla_resolve_hours: int | None = Field(default=None, ge=1, le=168)


def _generate_code(db: Session) -> str:
    """Generate next PROLINE code: PROLINE0001, PROLINE0002, ..."""
    max_id = db.execute(select(func.max(ProductLine.id))).scalar() or 0
    return f"PROLINE{(max_id + 1):04d}"


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_session)) -> list[SourceOut]:
    rows = db.execute(select(Source).order_by(Source.id)).scalars().all()
    return [SourceOut.model_validate(r) for r in rows]


@router.get("/product-lines", response_model=list[ProductLineOut])
def list_product_lines(db: Session = Depends(get_session)) -> list[ProductLineOut]:
    rows = db.execute(select(ProductLine).order_by(ProductLine.id)).scalars().all()
    # batch count modules per product line
    counts_q = db.execute(
        select(Module.product_line_code, func.count(Module.id))
        .group_by(Module.product_line_code)
    ).all()
    counts = {code: cnt for code, cnt in counts_q}

    result = []
    for r in rows:
        out = ProductLineOut(
            id=r.id,
            code=r.code,
            name=r.name,
            is_active=r.is_active,
            category=r.category,
            sla_reply_hours=r.sla_reply_hours,
            sla_resolve_hours=r.sla_resolve_hours,
            created_at=r.created_at.isoformat() if r.created_at else None,
            module_count=counts.get(r.code, 0),
        )
        result.append(out)
    return result


@router.post("/product-lines", response_model=ProductLineOut, status_code=201)
def add_product_line(
    body: ProductLineIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProductLineOut:
    """Create a new product_line with auto-generated PROLINE code."""
    if body.category not in CATEGORY_OPTIONS:
        raise HTTPException(status_code=422, detail=f"category must be one of {CATEGORY_OPTIONS}")

    code = _generate_code(db)
    pl = ProductLine(
        code=code,
        name=body.name,
        is_active=True,
        category=body.category,
        sla_reply_hours=body.sla_reply_hours,
        sla_resolve_hours=body.sla_resolve_hours,
    )
    db.add(pl)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"product_line already exists: {code}"
        ) from e
    db.refresh(pl)
    logger.info("admin_product_line_added", code=code, by=admin.user_id)
    return ProductLineOut(
        id=pl.id,
        code=pl.code,
        name=pl.name,
        is_active=pl.is_active,
        category=pl.category,
        sla_reply_hours=pl.sla_reply_hours,
        sla_resolve_hours=pl.sla_resolve_hours,
        created_at=pl.created_at.isoformat() if pl.created_at else None,
        module_count=0,
    )


@router.delete("/product-lines/{code}", status_code=204)
def delete_product_line(
    code: str,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    pl = db.execute(select(ProductLine).where(ProductLine.code == code)).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404, detail="product_line not found")
    has_modules = db.execute(
        select(Module).where(Module.product_line_code == code).limit(1)
    ).scalar_one_or_none()
    if has_modules is not None:
        raise HTTPException(
            status_code=409,
            detail=f"产品线「{pl.name}」下还有模块，请先删除所有模块",
        )
    # check tickets reference
    ticket_count = db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.product_line_code == code)
    ).scalar() or 0
    if ticket_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"产品线「{pl.name}」关联了 {ticket_count} 条工单，无法删除",
        )
    db.delete(pl)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"产品线「{pl.name}」仍有关联数据，无法删除",
        ) from e
    logger.info("admin_product_line_deleted", code=code, by=admin.user_id)
    return Response(status_code=204)


@router.patch("/product-lines/{code}", response_model=ProductLineOut)
def patch_product_line(
    code: str,
    body: ProductLinePatch,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProductLineOut:
    pl = db.execute(select(ProductLine).where(ProductLine.code == code)).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404, detail="product_line not found")

    patch = body.model_dump(exclude_unset=True)
    if "category" in patch and patch["category"] not in CATEGORY_OPTIONS:
        raise HTTPException(status_code=422, detail=f"category must be one of {CATEGORY_OPTIONS}")
    for field, value in patch.items():
        setattr(pl, field, value)
    db.commit()
    db.refresh(pl)
    module_count = db.execute(
        select(func.count(Module.id)).where(Module.product_line_code == code)
    ).scalar() or 0
    logger.info("admin_product_line_updated", code=code, by=admin.user_id, fields=list(patch.keys()))
    return ProductLineOut(
        id=pl.id,
        code=pl.code,
        name=pl.name,
        is_active=pl.is_active,
        category=pl.category,
        sla_reply_hours=pl.sla_reply_hours,
        sla_resolve_hours=pl.sla_resolve_hours,
        created_at=pl.created_at.isoformat() if pl.created_at else None,
        module_count=module_count,
    )
