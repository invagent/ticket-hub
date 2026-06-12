"""Linear push tests (D4) — gates, write-back, idempotency, error swallowing.

LinearClient is faked at the call site (injected via the `client` kwarg);
the GraphQL wire format itself is covered by tests/unit/adapters/.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.linear import CreatedIssue, LinearNetworkError
from app.config import get_settings
from app.models import HubIssue, Source, Ticket, User
from app.services.hub_issues.linear_push import push_hub_issue_to_linear


class _FakeLinearClient:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.requests: list[object] = []

    def create_issue(self, req):  # type: ignore[no-untyped-def]
        self.requests.append(req)
        if self._raises is not None:
            raise self._raises
        return CreatedIssue(
            id="uuid-123", identifier="ENG-42", url="https://linear.app/x/ENG-42", title=req.title
        )

    def close(self) -> None:
        pass


@pytest.fixture
def world(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("LINEAR_PUSH_ENABLED", "true")
    monkeypatch.setenv("LINEAR_API_KEY", "lk")
    monkeypatch.setenv("LINEAR_TEAM_ID", "team-1")
    get_settings.cache_clear()
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    yield db_session
    get_settings.cache_clear()


def _make_hub(db: Session, n: int, **overrides) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"HUB-LP-{n}",
        "type": "Bug_fix",
        "title": "开票失败",
        "canonical_body": "详细复现步骤",
        "status": "created",
        "priority": "high",
    }
    base.update(overrides)
    h = HubIssue(**base)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def test_push_writes_back_linear_fields(world: Session) -> None:
    hub = _make_hub(world, 1)
    fake = _FakeLinearClient()
    res = push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert res is not None
    assert res.linear_identifier == "ENG-42"

    world.refresh(hub)
    assert hub.linear_uuid == "uuid-123"
    assert hub.linear_identifier == "ENG-42"
    assert hub.linear_status_synced_at is not None

    req = fake.requests[0]
    assert req.title == f"[{hub.short_code}] 开票失败"  # type: ignore[attr-defined]
    assert req.priority == 2  # high  # type: ignore[attr-defined]
    assert req.team_id == "team-1"  # type: ignore[attr-defined]


def test_push_description_includes_source_tickets(world: Session) -> None:
    hub = _make_hub(world, 2)
    world.add(
        Ticket(
            short_code="TKT-LP-1",
            source_code="ksm",
            source_ticket_id="lp-1",
            type="Raw",
            status="received",
            title="x",
            hub_issue_id=hub.id,
        )
    )
    world.commit()
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    desc = fake.requests[0].description  # type: ignore[attr-defined]
    assert "TKT-LP-1 (ksm)" in desc
    assert hub.short_code in desc


def test_push_uses_assignee_linear_id(world: Session) -> None:
    world.add(User(id=5, feishu_uid="ou_a", name="alice", linear_user_id="lin-u-5"))
    world.commit()
    hub = _make_hub(world, 3, assigned_user_id=5)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert fake.requests[0].assignee_id == "lin-u-5"  # type: ignore[attr-defined]


def test_push_routes_to_assignee_team(world: Session) -> None:
    """Assignee with a linear_team_id → issue lands on THAT team, not default."""
    world.add(
        User(
            id=6,
            feishu_uid="ou_b",
            name="bob",
            linear_user_id="lin-u-6",
            linear_team_id="team-aralgo",
        )
    )
    world.commit()
    hub = _make_hub(world, 9, assigned_user_id=6)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert fake.requests[0].team_id == "team-aralgo"  # type: ignore[attr-defined]
    assert fake.requests[0].assignee_id == "lin-u-6"  # type: ignore[attr-defined]


def test_push_falls_back_to_default_team_for_group(world: Session) -> None:
    """Group assignee (no linear_team_id) → default team."""
    world.add(User(id=7, feishu_uid="ou_grp", name="数电开票组"))  # no linear mapping
    world.commit()
    hub = _make_hub(world, 10, assigned_user_id=7)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert fake.requests[0].team_id == "team-1"  # type: ignore[attr-defined]  # settings.linear_team_id
    assert fake.requests[0].assignee_id is None  # type: ignore[attr-defined]


def test_push_skips_operation_type(world: Session) -> None:
    hub = _make_hub(world, 4, type="Operation")
    fake = _FakeLinearClient()
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    assert fake.requests == []


def test_push_idempotent_on_linear_uuid(world: Session) -> None:
    hub = _make_hub(world, 5, linear_uuid="already", linear_identifier="ENG-1")
    fake = _FakeLinearClient()
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    assert fake.requests == []


def test_push_disabled_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_PUSH_ENABLED", "false")
    get_settings.cache_clear()
    hub = _make_hub(world, 6)
    fake = _FakeLinearClient()
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    assert fake.requests == []


def test_push_swallows_linear_errors(world: Session) -> None:
    hub = _make_hub(world, 7)
    fake = _FakeLinearClient(raises=LinearNetworkError("timeout"))
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    world.refresh(hub)
    assert hub.linear_uuid is None  # 可重试


def test_push_priority_default_zero(world: Session) -> None:
    hub = _make_hub(world, 8, priority=None)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert fake.requests[0].priority == 0  # type: ignore[attr-defined]
