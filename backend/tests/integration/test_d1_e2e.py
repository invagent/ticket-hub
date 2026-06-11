"""D1 end-to-end integration test against a real PostgreSQL container.

These tests run only when Docker is available. The session-scoped
`_pg_container_or_skip` fixture (in tests/conftest.py) handles skip logic.

What we exercise here that SQLite unit tests can't:
  - Partial unique indexes (decision spec §4.3 — `tickets_source_uniq` etc.)
  - PostgreSQL-specific JSON / JSONB behaviour
  - True transactional rollback semantics
  - Real CHECK constraints on hub_issues type / status combinations

Each test gets its own fresh PG schema via `pg_session` (function-scoped).
The TestClient overrides `get_session` to use that same session so webhooks
and reads see the same data.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.db import get_session
from app.models import (
    AssignmentScopeModule,
    NotificationLog,
    ProductLine,
    Source,
    Ticket,
    User,
)
from app.services.sla.escalation import EscalationWorker
from app.services.sla.watcher import SLAWatcher

pytestmark = pytest.mark.integration


def _bearer(uid: int = 1, *, role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pg_client(pg_session: Session) -> Iterator[TestClient]:
    """TestClient backed by the testcontainers PG session."""
    from app.main import create_app

    app = create_app()

    def _override() -> Iterator[Session]:
        try:
            yield pg_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---- ingest → list happy path --------------------------------------------


def test_ksm_webhook_ingest_to_list_e2e(pg_session: Session, pg_client: TestClient) -> None:
    """POST /webhook/ksm → GET /api/tickets shows the ticket on real PG."""
    pg_session.add(Source(code="ksm", name="KSM"))
    pg_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    pg_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    pg_session.flush()
    pg_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理")
    )
    pg_session.commit()

    resp = pg_client.post(
        "/webhook/ksm?access_token=test-token",
        json={
            "billId": "pg-bill-1",
            "title": "PG e2e",
            "account": "u1",
            "accountName": "alice",
            "erpUid": "ERP-PG",
            "productLineCode": "cloud-erp",
            "moduleName": "应付管理",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routing_decision"] == "assigned"
    assert body["assigned_user_ids"] == [1]
    ticket_id = body["ticket_id"]

    # GET /api/tickets/{id}
    detail = pg_client.get(f"/api/tickets/{ticket_id}", headers=_bearer())
    assert detail.status_code == 200
    body2 = detail.json()
    assert body2["short_code"] == body["short_code"]
    assert body2["assigned_user_id"] == 1
    assert body2["module"] == "应付管理"


def test_idempotent_dedup_via_partial_unique_index(
    pg_session: Session, pg_client: TestClient
) -> None:
    """Replay the same billId twice; second response has deduped=True.

    Real PG enforces the partial unique index. SQLite tests pass too, but only
    here can we be confident the index is wired correctly on production DDL.
    """
    pg_session.add(Source(code="ksm", name="KSM"))
    pg_session.commit()
    payload = {"billId": "pg-replay-001", "accountName": "x"}
    r1 = pg_client.post("/webhook/ksm?access_token=test-token", json=payload)
    r2 = pg_client.post("/webhook/ksm?access_token=test-token", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["deduped"] is False
    assert r2.json()["deduped"] is True
    assert r1.json()["ticket_id"] == r2.json()["ticket_id"]
    # Exactly one ticket row in PG
    assert pg_session.query(Ticket).count() == 1


# ---- cross-source customer identity merge --------------------------------


def test_cross_source_customer_match_via_erp_uid(
    pg_session: Session, pg_client: TestClient
) -> None:
    """KSM ticket → customer A. Then 智齿 ticket with same erp_uid → also A.

    Verifies that IdentityResolver's index lookups work on real PG indexes.
    """
    pg_session.add(Source(code="ksm", name="KSM"))
    pg_session.add(Source(code="zhichi", name="智齿"))
    pg_session.commit()

    pg_client.post(
        "/webhook/ksm?access_token=test-token",
        json={"billId": "pg-cross-1", "accountName": "x", "erpUid": "ERP-CROSS"},
    )
    r2 = pg_client.post(
        "/webhook/zhichi?access_token=test-token",
        json={
            "ticketid": "pg-cross-z1",
            "customerid": "z1",
            "customer": {"erp_uid": "ERP-CROSS"},
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()

    # Both tickets must point at the same customer (customer_identity_id differs,
    # but customer_id matches via the resolver).
    from app.models import CustomerIdentity

    idents = pg_session.query(CustomerIdentity).all()
    customer_ids = {i.customer_id for i in idents}
    assert len(customer_ids) == 1, f"expected single customer, got {customer_ids}"
    assert body2["routing_decision"] in ("assigned", "default_pool", "multi_match")


# ---- check constraints fire on PG ----------------------------------------


def test_pg_enforces_hub_issue_type_check(pg_session: Session) -> None:
    """Inserting a hub_issue with a bogus type must violate the CHECK constraint."""
    from sqlalchemy.exc import IntegrityError

    from app.models import HubIssue

    pg_session.add(HubIssue(short_code="BAD-1", type="NotARealType", title="x", status="created"))
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()


def test_pg_enforces_ticket_type_field_constraint(pg_session: Session) -> None:
    """A 'Child' type ticket must have parent_ticket_id + internal_split_id set."""
    from sqlalchemy.exc import IntegrityError

    pg_session.add(Source(code="ksm", name="KSM"))
    pg_session.commit()
    # Child without parent_ticket_id → CHECK violation
    pg_session.add(
        Ticket(
            short_code="CHILD-BAD",
            source_code=None,
            source_ticket_id=None,
            internal_split_id="x",  # Child rule: must have internal_split_id
            type="Child",
            status="received",
            parent_ticket_id=None,  # ❌ violates CHECK
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()


# ---- SLA pipeline against real PG ----------------------------------------


def test_sla_watcher_and_escalation_on_pg(pg_session: Session) -> None:
    """SLAWatcher writes notification_log → EscalationWorker promotes to deputy."""
    from datetime import UTC, datetime, timedelta

    from app.models import UserSupervisor

    pg_session.add(Source(code="ksm", name="KSM"))
    pg_session.add_all(
        [
            User(id=1, feishu_uid="ou_a", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_b", name="bob", role="assignee"),
            User(id=3, feishu_uid="ou_c", name="carol", role="supervisor"),
        ]
    )
    pg_session.flush()
    pg_session.add(UserSupervisor(user_id=1, supervisor_id=3, deputy_supervisor_id=2))
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    pg_session.add(
        Ticket(
            short_code="TKT-SLA",
            source_code="ksm",
            source_ticket_id="ksm-sla",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=8),  # well past 4h threshold
            assigned_user_id=1,
        )
    )
    pg_session.commit()

    # Step 1: SLAWatcher detects overdue and writes notification_log
    res = SLAWatcher(pg_session).scan(now=now)
    pg_session.commit()
    assert res.notifications_written == 1
    assert pg_session.query(NotificationLog).count() == 1

    # Step 2: Move time forward 3h; EscalationWorker promotes to deputy
    later = now + timedelta(hours=3)
    pg_session.execute(
        NotificationLog.__table__.update().values(
            sent_at=now  # explicit timestamp so escalation sees it as overdue
        )
    )
    pg_session.commit()

    er = EscalationWorker(pg_session).escalate_pending(now=later)
    pg_session.commit()
    assert len(er.escalated) == 1
    step = er.escalated[0]
    assert step.original_recipient_id == 1
    assert step.escalated_to_user_id == 2  # bob is the deputy
    assert step.via == "deputy"

    # Two notifications now: original (escalated) + new escalation row to bob
    notifs = pg_session.query(NotificationLog).all()
    assert len(notifs) == 2
    bob_notifs = [n for n in notifs if n.recipient_user_id == 2]
    assert len(bob_notifs) == 1
    assert bob_notifs[0].notify_type == "escalation"
