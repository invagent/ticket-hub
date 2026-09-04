"""confirm-classification/reclassify 新增的模块归类非空校验 + 审核后触发接管。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.config import get_settings
from app.models import HubIssue, Source, Ticket, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def gate_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _hub(db: Session, hid: int, *, module: str | None, type_: str = "Bug_fix") -> HubIssue:
    h = HubIssue(
        id=hid,
        short_code=f"HUB-{hid}",
        type=type_,
        title="t",
        canonical_body="b",
        status="pending_review",
        module=module,
        product_line_code="PL_A" if module else None,
    )
    db.add(h)
    db.flush()
    db.add(
        Ticket(
            id=hid * 10,
            short_code=f"TKT-{hid}",
            source_code="ksm",
            source_ticket_id=f"bill-{hid}",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=hid,
            predicted_type=type_,
        )
    )
    db.commit()
    return h


def test_confirm_rejects_empty_module(app_client: TestClient, gate_world: Session) -> None:
    _hub(gate_world, 90, module=None)
    r = app_client.post(
        "/api/supervisor/confirm-classification", json={"hub_issue_id": 90}, headers=_bearer(2)
    )
    assert r.status_code == 422, r.text
    assert "模块归类" in r.text


def test_confirm_rejects_fallback_module(app_client: TestClient, gate_world: Session) -> None:
    fallback = get_settings().module_fallback_module
    _hub(gate_world, 91, module=fallback)
    r = app_client.post(
        "/api/supervisor/confirm-classification", json={"hub_issue_id": 91}, headers=_bearer(2)
    )
    assert r.status_code == 422, r.text


def test_confirm_with_module_override_passes(app_client: TestClient, gate_world: Session) -> None:
    """审核时传 module 覆盖空值 → 校验通过，且覆盖值同步回关联 ticket。"""
    _hub(gate_world, 92, module=None)
    with patch("app.api.supervisor.peek_module_owner", lambda *a, **k: None):
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 92, "product_line_code": "PL_A", "module": "发票管理"},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    gate_world.expire_all()
    hub = gate_world.get(HubIssue, 92)
    assert hub.module == "发票管理"
    assert hub.product_line_code == "PL_A"
    tk = gate_world.get(Ticket, 920)
    assert tk.module == "发票管理"


def test_confirm_operation_with_module_triggers_takeover(
    app_client: TestClient, gate_world: Session
) -> None:
    """确认 Operation 分类（模块非空）→ 触发 KSM 接管 background task（对每条关联 ticket）。"""
    _hub(gate_world, 93, module="发票管理", type_="Operation")
    with patch("app.services.ksm.takeover.trigger_ksm_takeover_after_review") as mock_trigger:
        r = app_client.post(
            "/api/supervisor/confirm-classification", json={"hub_issue_id": 93}, headers=_bearer(2)
        )
    assert r.status_code == 200, r.text
    mock_trigger.assert_called_once_with(930)


def test_reclassify_rejects_empty_module(app_client: TestClient, gate_world: Session) -> None:
    _hub(gate_world, 94, module=None)
    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 94, "new_type": "Operation", "reason": "x"},
        headers=_bearer(2),
    )
    assert r.status_code == 422, r.text


def test_reclassify_with_module_present_triggers_takeover(
    app_client: TestClient, gate_world: Session
) -> None:
    _hub(gate_world, 95, module="发票管理")
    with patch("app.services.ksm.takeover.trigger_ksm_takeover_after_review") as mock_trigger:
        r = app_client.post(
            "/api/supervisor/reclassify",
            json={"hub_issue_id": 95, "new_type": "Operation", "reason": "x"},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    mock_trigger.assert_called_once_with(950)
