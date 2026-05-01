"""Admin endpoints (users / scopes / sources / product_lines).

D0: read-only listing for sources + product_lines + users (basic).
Full CRUD + Feishu directory sync land in D1 (decision D19).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ProductLine, Source, User

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


class UserOut(BaseModel):
    id: int
    feishu_uid: str
    employee_no: str | None
    name: str
    email: str | None
    role: str
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


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_session)) -> list[UserOut]:
    rows = (
        db.execute(select(User).where(User.deleted_at.is_(None)).order_by(User.id)).scalars().all()
    )
    return [UserOut.model_validate(r) for r in rows]
