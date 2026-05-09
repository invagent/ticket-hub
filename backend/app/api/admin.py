"""Admin endpoints (sources / product_lines).

D0: read-only listing for sources + product_lines.
D1: users moved to admin_users.py (full CRUD).
D2: scopes in admin_scopes.py (full CRUD).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ProductLine, Source

router = APIRouter()


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

    model_config = {"from_attributes": True}


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_session)) -> list[SourceOut]:
    rows = db.execute(select(Source).order_by(Source.id)).scalars().all()
    return [SourceOut.model_validate(r) for r in rows]


@router.get("/product-lines", response_model=list[ProductLineOut])
def list_product_lines(db: Session = Depends(get_session)) -> list[ProductLineOut]:
    rows = db.execute(select(ProductLine).order_by(ProductLine.id)).scalars().all()
    return [ProductLineOut.model_validate(r) for r in rows]
