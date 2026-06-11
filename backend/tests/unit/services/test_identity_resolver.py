"""IdentityResolver unit tests against in-memory SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC

import pytest
from sqlalchemy.orm import Session

from app.models import Customer, CustomerIdentity, Source
from app.services.identity.resolver import IdentityInput, IdentityResolver


@pytest.fixture
def seeded_db(db_session: Session) -> Iterator[Session]:
    db_session.add_all(
        [
            Source(code="ksm", name="KSM"),
            Source(code="zhichi", name="智齿"),
            Source(code="zammad", name="Zammad"),
        ]
    )
    db_session.commit()
    yield db_session


@pytest.fixture
def existing_alice(seeded_db: Session) -> tuple[Customer, CustomerIdentity]:
    """Pre-existing customer + KSM identity for alice."""
    cust = Customer(display_name="alice")
    seeded_db.add(cust)
    seeded_db.flush()
    ident = CustomerIdentity(
        customer_id=cust.id,
        source_code="ksm",
        source_user_id="ksm-user-1",
        erp_uid="ERP-100",
        email="alice@example.com",
        mobile="13800138001",
        source_custom_id="zammad-cust-1",
        raw_name="alice",
        resolved_by_key="manual",
    )
    seeded_db.add(ident)
    seeded_db.commit()
    return cust, ident


# ---- New customer path -----------------------------------------------------


def test_total_miss_creates_new_customer(seeded_db: Session) -> None:
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="ksm",
            source_user_id="brand-new",
            erp_uid="ERP-NEW",
            email="bob@example.com",
            mobile="13900139000",
            raw_name="bob",
        )
    )
    assert result.created_new is True
    assert result.resolved_by_key == "none"
    assert result.customer_id > 0
    seeded_db.commit()
    cust = seeded_db.get(Customer, result.customer_id)
    assert cust is not None
    assert cust.display_name == "bob"


# ---- Priority order: erp_uid > mobile > email > source_custom_id ----------


def test_erp_uid_match_wins(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, _ = existing_alice
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="zhichi",
            source_user_id="zhichi-user-99",
            erp_uid="ERP-100",
            email="someone-else@example.com",  # different email
            mobile="19900000000",  # different mobile
        )
    )
    assert result.created_new is False
    assert result.resolved_by_key == "erp_uid"
    assert result.customer_id == cust.id


def test_mobile_match_when_no_erp_uid(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, _ = existing_alice
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="zhichi",
            source_user_id="zhichi-user-2",
            mobile="13800138001",
            email="not-known@example.com",
        )
    )
    assert result.resolved_by_key == "mobile"
    assert result.customer_id == cust.id


def test_email_match_when_no_higher_keys(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, _ = existing_alice
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="zhichi",
            source_user_id="zhichi-user-3",
            email="alice@example.com",
        )
    )
    assert result.resolved_by_key == "email"
    assert result.customer_id == cust.id


def test_source_custom_id_match_same_source_only(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, _ = existing_alice
    r = IdentityResolver(seeded_db)
    # alice has source_custom_id='zammad-cust-1' on the KSM row.
    # source_custom_id matching is scoped to (source_code, source_custom_id),
    # so a KSM lookup with same custom_id matches; zammad lookup does not.
    same_source = r.resolve(IdentityInput(source_code="ksm", source_custom_id="zammad-cust-1"))
    assert same_source.resolved_by_key == "source_custom_id"
    assert same_source.customer_id == cust.id


def test_source_custom_id_does_not_match_across_sources(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    r = IdentityResolver(seeded_db)
    cross_source = r.resolve(IdentityInput(source_code="zammad", source_custom_id="zammad-cust-1"))
    assert cross_source.resolved_by_key == "none"
    assert cross_source.created_new is True


# ---- Strong-key wins on conflict ------------------------------------------


def test_strong_key_wins_when_keys_point_at_different_customers(
    seeded_db: Session,
) -> None:
    """erp_uid points at customer_A, mobile points at customer_B → erp_uid wins."""
    cust_a = Customer(display_name="A")
    cust_b = Customer(display_name="B")
    seeded_db.add_all([cust_a, cust_b])
    seeded_db.flush()
    seeded_db.add_all(
        [
            CustomerIdentity(
                customer_id=cust_a.id,
                source_code="ksm",
                source_user_id="A-ksm",
                erp_uid="ERP-A",
                resolved_by_key="manual",
            ),
            CustomerIdentity(
                customer_id=cust_b.id,
                source_code="ksm",
                source_user_id="B-ksm",
                mobile="13700137000",
                resolved_by_key="manual",
            ),
        ]
    )
    seeded_db.commit()

    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="zhichi",
            source_user_id="X-zhichi",
            erp_uid="ERP-A",
            mobile="13700137000",
        )
    )
    # erp_uid > mobile in priority
    assert result.resolved_by_key == "erp_uid"
    assert result.customer_id == cust_a.id


# ---- Same-source exact identity short-circuit ------------------------------


def test_same_source_user_id_returns_existing_identity(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, ident = existing_alice
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="ksm",
            source_user_id="ksm-user-1",
            erp_uid="will-be-ignored",
        )
    )
    assert result.customer_id == cust.id
    assert result.customer_identity_id == ident.id
    assert result.resolved_by_key == "manual"  # unchanged
    assert result.created_new is False


# ---- New identity row created when matching cross-source ------------------


def test_match_creates_new_identity_for_other_source(
    seeded_db: Session, existing_alice: tuple[Customer, CustomerIdentity]
) -> None:
    cust, ident_old = existing_alice
    r = IdentityResolver(seeded_db)
    result = r.resolve(
        IdentityInput(
            source_code="zhichi",
            source_user_id="zhichi-user-X",
            erp_uid="ERP-100",
        )
    )
    assert result.customer_id == cust.id
    assert result.customer_identity_id != ident_old.id  # new row
    seeded_db.commit()
    new_ident = seeded_db.get(CustomerIdentity, result.customer_identity_id)
    assert new_ident is not None
    assert new_ident.source_code == "zhichi"
    assert new_ident.resolved_by_key == "erp_uid"


# ---- Soft-deleted identities are ignored -----------------------------------


def test_soft_deleted_identity_not_matched(seeded_db: Session) -> None:
    from datetime import datetime

    cust = Customer(display_name="ghost")
    seeded_db.add(cust)
    seeded_db.flush()
    seeded_db.add(
        CustomerIdentity(
            customer_id=cust.id,
            source_code="ksm",
            source_user_id="ghost-ksm",
            erp_uid="ERP-GHOST",
            resolved_by_key="manual",
            deleted_at=datetime.now(UTC),
        )
    )
    seeded_db.commit()

    r = IdentityResolver(seeded_db)
    result = r.resolve(IdentityInput(source_code="zhichi", erp_uid="ERP-GHOST"))
    assert result.created_new is True  # ghost row should be ignored
