"""产品内提单租户管理（ADR-0017 D4，require_admin）。

hmac_secret 只在创建 / 轮换密钥的响应中回显一次，列表与详情不返回。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import ProductLine, Tenant, TenantUser, Ticket

router = APIRouter()
logger = get_logger(__name__)


class TenantOut(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool
    default_product_line_code: str | None
    user_count: int = 0
    ticket_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantCreated(TenantOut):
    hmac_secret: str  # 仅创建/轮换时回显一次


class TenantIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=128)
    default_product_line_code: str | None = None


class TenantPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    is_active: bool | None = None
    default_product_line_code: str | None = None


class TenantUserOut(BaseModel):
    id: int
    external_uid: str
    name: str | None
    mobile: str | None
    email: str | None
    customer_identity_id: int | None
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _new_secret() -> str:
    return secrets.token_hex(32)


def _counts(db: Session, tenant_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    if not tenant_ids:
        return {}, {}
    urows = db.execute(
        select(TenantUser.tenant_id, func.count(TenantUser.id))
        .where(TenantUser.tenant_id.in_(tenant_ids))
        .group_by(TenantUser.tenant_id)
    ).all()
    trows = db.execute(
        select(TenantUser.tenant_id, func.count(Ticket.id))
        .join(Ticket, Ticket.tenant_user_id == TenantUser.id)
        .where(TenantUser.tenant_id.in_(tenant_ids), Ticket.deleted_at.is_(None))
        .group_by(TenantUser.tenant_id)
    ).all()
    return {r[0]: int(r[1]) for r in urows}, {r[0]: int(r[1]) for r in trows}


def _out(t: Tenant, users: int = 0, tickets: int = 0) -> TenantOut:
    o = TenantOut.model_validate(t)
    o.user_count = users
    o.ticket_count = tickets
    return o


def _check_pl(db: Session, code: str | None) -> None:
    if code is None:
        return
    if db.execute(select(ProductLine).where(ProductLine.code == code)).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="product_line not found")


@router.get("", response_model=list[TenantOut])
def list_tenants(
    _admin: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> list[TenantOut]:
    rows = list(db.execute(select(Tenant).order_by(Tenant.id)).scalars())
    users, tickets = _counts(db, [r.id for r in rows])
    return [_out(r, users.get(r.id, 0), tickets.get(r.id, 0)) for r in rows]


@router.post("", response_model=TenantCreated, status_code=201)
def create_tenant(
    body: TenantIn, admin: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> TenantCreated:
    _check_pl(db, body.default_product_line_code)
    secret = _new_secret()
    row = Tenant(
        code=body.code,
        name=body.name,
        hmac_secret=secret,
        is_active=True,
        default_product_line_code=body.default_product_line_code,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"tenant code already exists: {body.code}"
        ) from e
    db.refresh(row)
    logger.info("admin_tenant_created", id=row.id, code=row.code, by=admin.user_id)
    return TenantCreated(**_out(row).model_dump(), hmac_secret=secret)


@router.patch("/{tenant_id}", response_model=TenantOut)
def patch_tenant(
    tenant_id: int,
    body: TenantPatch,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> TenantOut:
    row = db.get(Tenant, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    patch = body.model_dump(exclude_unset=True)
    if "default_product_line_code" in patch:
        _check_pl(db, patch["default_product_line_code"])
    for k, v in patch.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    logger.info("admin_tenant_patched", id=row.id, fields=list(patch), by=admin.user_id)
    users, tickets = _counts(db, [row.id])
    return _out(row, users.get(row.id, 0), tickets.get(row.id, 0))


@router.post("/{tenant_id}/rotate-secret", response_model=TenantCreated)
def rotate_secret(
    tenant_id: int, admin: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> TenantCreated:
    row = db.get(Tenant, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    row.hmac_secret = _new_secret()
    db.commit()
    db.refresh(row)
    logger.info("admin_tenant_secret_rotated", id=row.id, by=admin.user_id)
    return TenantCreated(**_out(row).model_dump(), hmac_secret=row.hmac_secret)


@router.get("/{tenant_id}/users", response_model=list[TenantUserOut])
def list_tenant_users(
    tenant_id: int, _admin: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> list[TenantUserOut]:
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    rows = db.execute(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant_id)
        .order_by(TenantUser.id.desc())
        .limit(500)
    ).scalars()
    return [TenantUserOut.model_validate(r) for r in rows]
