"""Admin endpoints (sources / product_lines).

D0: read-only listing for sources + product_lines.
D1: users moved to admin_users.py (full CRUD).
D2: scopes in admin_scopes.py (full CRUD).
D2-C: per-product-line SLA threshold PATCH.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import ProductLine, Source

router = APIRouter()
logger = get_logger(__name__)


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
    sla_reply_hours: int | None = None
    sla_resolve_hours: int | None = None

    model_config = {"from_attributes": True}


class ProductLinePatch(BaseModel):
    """PATCH body for /api/admin/product-lines/{code}.

    NULL on either field clears the override (falls back to SLAWatcher
    defaults). Pass `0` is rejected — use `null` to clear.
    """

    sla_reply_hours: int | None = Field(default=None, ge=1, le=168)
    sla_resolve_hours: int | None = Field(default=None, ge=1, le=168)
    is_active: bool | None = None


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_session)) -> list[SourceOut]:
    rows = db.execute(select(Source).order_by(Source.id)).scalars().all()
    return [SourceOut.model_validate(r) for r in rows]


@router.get("/product-lines", response_model=list[ProductLineOut])
def list_product_lines(db: Session = Depends(get_session)) -> list[ProductLineOut]:
    rows = db.execute(select(ProductLine).order_by(ProductLine.id)).scalars().all()
    return [ProductLineOut.model_validate(r) for r in rows]


@router.patch("/product-lines/{code}", response_model=ProductLineOut)
def patch_product_line(
    code: str,
    body: ProductLinePatch,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProductLineOut:
    """Update SLA overrides (and is_active) for a product line.

    Send `null` to a field to clear the override (revert to SLAWatcher
    builtin default).
    """
    pl = db.execute(select(ProductLine).where(ProductLine.code == code)).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404, detail="product_line not found")

    patch = body.model_dump(exclude_unset=True)
    for field, value in patch.items():
        setattr(pl, field, value)
    db.commit()
    db.refresh(pl)
    logger.info(
        "admin_product_line_updated",
        code=code,
        by=admin.user_id,
        fields=list(patch.keys()),
        sla_reply_hours=pl.sla_reply_hours,
        sla_resolve_hours=pl.sla_resolve_hours,
    )
    return ProductLineOut.model_validate(pl)
