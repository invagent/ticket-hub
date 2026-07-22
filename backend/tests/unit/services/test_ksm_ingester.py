"""KSMIngester unit tests + webhook e2e."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeModule,
    Customer,
    CustomerIdentity,
    ProductLine,
    Source,
    StatusHistory,
    Ticket,
    User,
)
from app.services.ingest.ksm_ingester import IngestError, KSMIngester


@pytest.fixture
def ingest_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=99, feishu_uid="ou_pool", name="pool", role="supervisor"),
        ]
    )
    db_session.commit()
    return db_session


def _payload(**overrides) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "billId": "ksm-bill-001",
        "title": "应付审核报错",
        "content": "审核时弹出空指针",
        "account": "user-acc-001",
        "accountName": "甲方甲",
        "email": "buyer@example.com",
        "mobile": "13800138001",
        "erpUid": "ERP-AAA",
        "productLineCode": "cloud-erp",
        "moduleName": "应付管理",
    }
    base.update(overrides)
    return base


# ---- happy path -----------------------------------------------------------


def test_first_ingest_creates_customer_and_routes(ingest_world: Session) -> None:
    ingest_world.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理")
    )
    ingest_world.commit()

    res = KSMIngester(ingest_world).ingest(_payload())
    ingest_world.commit()

    assert res.deduped is False
    assert res.short_code.startswith("TKT-")
    assert res.routing_decision == "assigned"
    assert res.assigned_user_ids == [1]

    ticket = ingest_world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.assigned_user_id == 1
    assert ticket.status == "received"
    assert ticket.type == "Raw"
    assert ticket.source_code == "ksm"
    assert ticket.source_ticket_id == "ksm-bill-001"
    assert ticket.module == "应付管理"

    # customer + identity created
    cust = ingest_world.get(Customer, res.customer_id)
    assert cust is not None
    ident = ingest_world.get(CustomerIdentity, res.customer_identity_id)
    assert ident is not None
    assert ident.erp_uid == "ERP-AAA"

    # status history written
    histories = ingest_world.query(StatusHistory).all()
    assert len(histories) == 1
    h = histories[0]
    assert h.entity_type == "ticket"
    assert h.entity_id == ticket.id
    assert h.from_status is None
    assert h.to_status == "received"
    assert h.changed_by == "system:ingest"


def test_idempotent_replay_returns_dedup(ingest_world: Session) -> None:
    """Same billId twice → second call returns deduped=True, doesn't re-insert."""
    KSMIngester(ingest_world).ingest(_payload())
    ingest_world.commit()
    res2 = KSMIngester(ingest_world).ingest(_payload())
    ingest_world.commit()

    assert res2.deduped is True
    assert ingest_world.query(Ticket).count() == 1
    assert ingest_world.query(Customer).count() == 1


def test_existing_customer_matched_by_erp_uid(ingest_world: Session) -> None:
    """Pre-existing customer with matching erp_uid → ticket linked, no new customer."""
    cust = Customer(display_name="known")
    ingest_world.add(cust)
    ingest_world.flush()
    ingest_world.add(
        CustomerIdentity(
            customer_id=cust.id,
            source_code="zhichi",
            source_user_id="zhichi-known",
            erp_uid="ERP-AAA",
            resolved_by_key="manual",
        )
    )
    ingest_world.commit()

    res = KSMIngester(ingest_world).ingest(_payload())
    ingest_world.commit()
    assert res.customer_id == cust.id
    # New identity row materialized for KSM source pointing at known customer
    new_ident = ingest_world.get(CustomerIdentity, res.customer_identity_id)
    assert new_ident is not None
    assert new_ident.source_code == "ksm"
    assert new_ident.resolved_by_key == "erp_uid"


# ---- routing branches -----------------------------------------------------


def test_no_route_match_falls_to_default_pool(ingest_world: Session) -> None:
    res = KSMIngester(ingest_world, default_pool_user_id=99).ingest(_payload())
    ingest_world.commit()
    assert res.routing_decision == "default_pool"
    assert res.assigned_user_ids == [99]
    ticket = ingest_world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.assigned_user_id == 99


def test_multi_match_leaves_assigned_null(ingest_world: Session) -> None:
    """2 partner-less users own the same module → multi_match. ticket.assigned_user_id stays NULL."""
    ingest_world.add_all(
        [
            AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理"),
            AssignmentScopeModule(user_id=99, product_line_code="cloud-erp", module="应付管理"),
        ]
    )
    ingest_world.commit()

    res = KSMIngester(ingest_world).ingest(_payload())
    ingest_world.commit()
    assert res.routing_decision == "multi_match"
    ticket = ingest_world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.assigned_user_id is None


# ---- validation ----------------------------------------------------------


def test_missing_billId_raises(ingest_world: Session) -> None:
    with pytest.raises(IngestError, match="billId"):
        KSMIngester(ingest_world).ingest(_payload(billId=""))


def test_billId_must_be_string(ingest_world: Session) -> None:
    payload = _payload()
    payload["billId"] = 12345  # type: ignore[assignment]
    with pytest.raises(IngestError, match="billId"):
        KSMIngester(ingest_world).ingest(payload)


# ---- webhook e2e via TestClient -------------------------------------------


def test_webhook_ksm_e2e_full_payload(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: POST /webhook/ksm with FULL payload (legacy / test path).

    Per D2-F: KSM webhook always returns {"code": 0}; verify ingest by
    querying DB instead of inspecting response shape.
    """
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.flush()
    db_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理")
    )
    db_session.commit()

    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={
            "billId": "ksm-bill-e2e",
            "title": "e2e",
            "account": "u",
            "accountName": "alice",
            "email": "alice@example.com",
            "erpUid": "ERP-E2E",
            "productLineCode": "cloud-erp",
            "moduleName": "应付管理",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"code": 0}

    # Verify ingest by query
    t = db_session.query(Ticket).filter_by(source_ticket_id="ksm-bill-e2e").one()
    assert t.assigned_user_id == 1
    assert t.product_line_code == "cloud-erp"


def test_webhook_ksm_invalid_token_returns_401(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(
        "/webhook/ksm?access_token=wrong",
        json={"billId": "x"},
    )
    assert resp.status_code == 401


def test_webhook_ksm_missing_billId_silently_acks(app_client) -> None:  # type: ignore[no-untyped-def]
    """Per KSM doc: validate fields; if missing, log + ignore. Don't 4xx
    (so KSM doesn't retry malformed pushes)."""
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json={"title": "no billId"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}


def test_webhook_ksm_non_object_payload_returns_400(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(
        "/webhook/ksm?access_token=test-token",
        json=["not", "an", "object"],
    )
    assert resp.status_code == 400


def test_webhook_ksm_idempotent_replay(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    """Replay the same billId via full-payload webhook — second call dedups."""
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()

    payload = {"billId": "replay-001", "accountName": "x"}
    r1 = app_client.post("/webhook/ksm?access_token=test-token", json=payload)
    r2 = app_client.post("/webhook/ksm?access_token=test-token", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == {"code": 0}
    assert r2.json() == {"code": 0}
    # Only one ticket exists despite two webhooks
    assert db_session.query(Ticket).filter_by(source_ticket_id="replay-001").count() == 1


def _seed_existing_with_hub(db_session, *, op_status, bill_id, short_code, hub_short_code):  # type: ignore[no-untyped-def]
    from app.models import HubIssue, Source, Ticket

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    hub = HubIssue(
        short_code=hub_short_code,
        type="Operation",
        title="t",
        canonical_body="旧内容",
        status="created",
        op_status=op_status,
        op_handler="agent",
    )
    db_session.add(hub)
    db_session.flush()
    existing = Ticket(
        short_code=short_code,
        source_code="ksm",
        source_ticket_id=bill_id,
        type="Raw",
        status="received",
        source_payload={"billId": bill_id},
        title="t",
        body="b",
        hub_issue_id=hub.id,
    )
    db_session.add(existing)
    db_session.commit()
    return existing, hub


def test_ingest_resupply_on_supplementing(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已存在 ticket 且 hub.op_status=supplementing → 补料重提：content_refresh + op_status→resupplied/agent。"""
    from app.services.hub_issues.op_status import OP_RESUPPLIED, OP_SUPPLEMENTING
    from app.services.ingest import ksm_ingester as mod

    existing, hub = _seed_existing_with_hub(
        db_session,
        op_status=OP_SUPPLEMENTING,
        bill_id="bill-resupply-1",
        short_code="TKT-SR-1",
        hub_short_code="HUB-SR-1",
    )

    called: dict = {}

    def fake_refresh(db, ticket, payload):
        called["ticket_id"] = ticket.id
        called["payload"] = payload
        return True

    monkeypatch.setattr(mod, "apply_content_refresh", fake_refresh)
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-resupply-1", "content": "新补料"})
    db_session.commit()

    assert called["ticket_id"] == existing.id
    assert called["payload"]["content"] == "新补料"
    assert result.deduped is True
    assert result.ticket_id == existing.id

    db_session.refresh(hub)
    assert hub.op_status == OP_RESUPPLIED
    assert hub.op_handler == "agent"


def test_ingest_reject_on_answered(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已存在 ticket 且 hub.op_status=answered → 驳回：content_refresh + op_status→processing/主管 + reject_count+1。"""
    from app.services.hub_issues.op_status import OP_ANSWERED, OP_PROCESSING
    from app.services.ingest import ksm_ingester as mod

    existing, hub = _seed_existing_with_hub(
        db_session,
        op_status=OP_ANSWERED,
        bill_id="bill-reject-1",
        short_code="TKT-RJ-1",
        hub_short_code="HUB-RJ-1",
    )
    assert hub.reject_count == 0

    called: dict = {}
    monkeypatch.setattr(
        mod,
        "apply_content_refresh",
        lambda db, ticket, payload: called.setdefault("ticket_id", ticket.id) or True,
    )
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-reject-1", "content": "客户不满意"})
    db_session.commit()

    assert called["ticket_id"] == existing.id
    assert result.deduped is True
    assert result.ticket_id == existing.id

    db_session.refresh(hub)
    assert hub.op_status == OP_PROCESSING
    assert hub.reject_count == 1
    assert hub.op_handler == "主管"  # default_pool_user_id 未配 → 兜底 "主管"


def test_ingest_noop_on_closed(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已存在 ticket 且 hub.op_status=closed（硬终态）→ 原 no-op，不调 content_refresh。"""
    from app.services.hub_issues.op_status import OP_CLOSED
    from app.services.ingest import ksm_ingester as mod

    existing, hub = _seed_existing_with_hub(
        db_session,
        op_status=OP_CLOSED,
        bill_id="bill-closed-1",
        short_code="TKT-CL-1",
        hub_short_code="HUB-CL-1",
    )

    called = {"n": 0}
    monkeypatch.setattr(mod, "apply_content_refresh", lambda *a, **k: called.__setitem__("n", 1))
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-closed-1", "content": "又发一遍"})
    db_session.commit()

    assert called["n"] == 0
    assert result.deduped is True
    assert result.ticket_id == existing.id
    db_session.refresh(hub)
    assert hub.op_status == OP_CLOSED  # 未变


def test_ingest_dedup_noop_when_no_hub(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已存在 ticket 但未毕业 hub（重复心跳）→ 原 no-op，不调 content_refresh。"""
    from app.models import Source, Ticket
    from app.services.ingest import ksm_ingester as mod

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    existing = Ticket(
        short_code="TKT-SR-2",
        source_code="ksm",
        source_ticket_id="bill-refill-2",
        type="Raw",
        status="received",
        source_payload={"billId": "bill-refill-2"},
        title="t",
        body="b",
    )
    db_session.add(existing)
    db_session.commit()

    called = {"n": 0}
    monkeypatch.setattr(mod, "apply_content_refresh", lambda *a, **k: called.__setitem__("n", 1))
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-refill-2", "content": "重复心跳"})
    assert called["n"] == 0
    assert result.deduped is True
    assert result.ticket_id == existing.id
