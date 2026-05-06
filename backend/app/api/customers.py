"""GET /api/customers — search + identity graph.

  GET /api/customers/search?q=&limit=         search by name/email/mobile/erp_uid
  GET /api/customers/{customer_id}            customer + identities + merge chain

Auth: any logged-in user. (D2 may add row-level visibility.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.repositories.customer import CustomerRepository

router = APIRouter()


class CustomerSummary(BaseModel):
    id: int
    display_name: str | None
    company: str | None
    primary_contact: dict[str, Any] | None
    merged_into_customer_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class IdentityOut(BaseModel):
    id: int
    customer_id: int
    source_code: str
    source_user_id: str | None
    source_custom_id: str | None
    erp_uid: str | None
    email: str | None
    mobile: str | None
    raw_name: str | None
    resolved_by_key: str
    human_confirmed: bool
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class CustomerDetail(BaseModel):
    """Full identity-graph payload for /customers/:id detail page."""

    customer: CustomerSummary
    identities: list[IdentityOut]
    merged_into_chain: list[int]


@router.get("/search", response_model=list[CustomerSummary])
def search_customers(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    q: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(20, ge=1, le=100),
) -> list[CustomerSummary]:
    rows = CustomerRepository(db).search(q=q, limit=limit)
    return [CustomerSummary.model_validate(r) for r in rows]


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(
    customer_id: int,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> CustomerDetail:
    repo = CustomerRepository(db)
    customer = repo.get(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return CustomerDetail(
        customer=CustomerSummary.model_validate(customer),
        identities=[IdentityOut.model_validate(i) for i in repo.list_identities(customer_id)],
        merged_into_chain=repo.get_merged_into_chain(customer_id),
    )
