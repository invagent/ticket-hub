"""Tests for /api/customers/search and /api/customers/{id}."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Customer, CustomerIdentity, Source


def _bearer(user_id: int = 1, *, role: str = "assignee") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.commit()

    # 3 customers; alice has 2 identities (ksm + zhichi), bob 1, charlie 1.
    # bob is "merged into" alice (merged_into_customer_id chain).
    db_session.add_all(
        [
            Customer(id=1, display_name="alice"),
            Customer(id=2, display_name="bob", merged_into_customer_id=1),
            Customer(id=3, display_name="charlie"),
            Customer(id=4, display_name="ghost", deleted_at=datetime.now(UTC)),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            CustomerIdentity(
                customer_id=1,
                source_code="ksm",
                source_user_id="ksm-alice",
                erp_uid="ERP-A",
                email="alice@example.com",
                mobile="13800138001",
                resolved_by_key="manual",
            ),
            CustomerIdentity(
                customer_id=1,
                source_code="zhichi",
                source_user_id="zh-alice",
                erp_uid="ERP-A",
                email="alice@example.com",
                resolved_by_key="erp_uid",
            ),
            CustomerIdentity(
                customer_id=2,
                source_code="ksm",
                source_user_id="ksm-bob",
                erp_uid="ERP-B",
                mobile="13900139000",
                resolved_by_key="manual",
            ),
            CustomerIdentity(
                customer_id=3,
                source_code="zhichi",
                source_user_id="zh-charlie",
                email="charlie@example.com",
                resolved_by_key="manual",
            ),
            # soft-deleted identity
            CustomerIdentity(
                customer_id=1,
                source_code="ksm",
                source_user_id="ksm-alice-old",
                email="old-alice@example.com",
                resolved_by_key="manual",
                deleted_at=datetime.now(UTC),
            ),
        ]
    )
    db_session.commit()
    return db_session


# ---- auth ----------------------------------------------------------------


def test_search_requires_auth(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/customers/search?q=alice").status_code == 401


def test_get_customer_requires_auth(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/customers/1").status_code == 401


# ---- /search ------------------------------------------------------------


def test_search_by_display_name_substring(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/customers/search?q=ali", headers=_bearer())
    assert r.status_code == 200
    names = {c["display_name"] for c in r.json()}
    assert "alice" in names


def test_search_by_email_exact(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/customers/search?q=charlie@example.com", headers=_bearer())
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert 3 in ids


def test_search_by_mobile_exact(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/customers/search?q=13900139000", headers=_bearer())
    ids = [c["id"] for c in r.json()]
    assert 2 in ids


def test_search_by_erp_uid_returns_only_one_customer_when_two_identities_share_it(
    app_client: TestClient, world: Session
) -> None:
    """alice has TWO identities sharing erp_uid='ERP-A' — search must dedup to one customer row."""
    r = app_client.get("/api/customers/search?q=ERP-A", headers=_bearer())
    rows = r.json()
    ids = [c["id"] for c in rows]
    assert ids.count(1) == 1


def test_search_excludes_soft_deleted_customer(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/customers/search?q=ghost", headers=_bearer())
    assert r.json() == []


def test_search_partial_mobile_does_not_match(app_client: TestClient, world: Session) -> None:
    """Exact-match policy: '139' is too short, must NOT match all 139xx mobiles."""
    r = app_client.get("/api/customers/search?q=139", headers=_bearer())
    rows = r.json()
    # the only way "139" hits is via display_name LIKE — none of our customers contain "139"
    assert all(c["id"] in {1, 2, 3} or c["display_name"] is None for c in rows)
    # explicitly: no row whose only relation is mobile starting with 139
    # (all rows here must hit display_name substring, which "139" doesn't)
    assert rows == []


def test_search_empty_q_rejected(app_client: TestClient, world: Session) -> None:
    # FastAPI Query(..., min_length=1) → 422
    assert app_client.get("/api/customers/search?q=", headers=_bearer()).status_code == 422


def test_search_limit_capped_at_100(app_client: TestClient, world: Session) -> None:
    # limit > 100 should 422 (Query le=100)
    r = app_client.get("/api/customers/search?q=alice&limit=500", headers=_bearer())
    assert r.status_code == 422


# ---- /{customer_id} -----------------------------------------------------


def test_get_customer_returns_full_graph(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/customers/1", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["customer"]["id"] == 1
    assert body["customer"]["display_name"] == "alice"
    # alice has 2 active identities; soft-deleted one excluded
    assert len(body["identities"]) == 2
    sources = {i["source_code"] for i in body["identities"]}
    assert sources == {"ksm", "zhichi"}
    # alice is the merge target → no merged_into chain
    assert body["merged_into_chain"] == []


def test_get_customer_with_merged_into_chain(app_client: TestClient, world: Session) -> None:
    """bob is merged into alice; chain should show [alice.id]."""
    r = app_client.get("/api/customers/2", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["customer"]["id"] == 2
    assert body["customer"]["merged_into_customer_id"] == 1
    assert body["merged_into_chain"] == [1]


def test_get_customer_unknown_returns_404(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/customers/9999", headers=_bearer()).status_code == 404


def test_get_customer_soft_deleted_returns_404(app_client: TestClient, world: Session) -> None:
    assert app_client.get("/api/customers/4", headers=_bearer()).status_code == 404


def test_merge_chain_handles_cycle(
    app_client: TestClient, world: Session, db_session: Session
) -> None:
    """Defensive: bad data with a cycle (a→b→a) must not loop forever."""
    db_session.add_all(
        [
            Customer(id=10, display_name="A", merged_into_customer_id=11),
            Customer(id=11, display_name="B", merged_into_customer_id=10),
        ]
    )
    db_session.commit()
    r = app_client.get("/api/customers/10", headers=_bearer())
    assert r.status_code == 200
    chain = r.json()["merged_into_chain"]
    # Should walk a→b but stop before re-visiting a
    assert chain == [11]
