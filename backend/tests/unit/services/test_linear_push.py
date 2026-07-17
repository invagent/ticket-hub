"""Linear push tests (D4) — gates, write-back, idempotency, error swallowing.

LinearClient is faked at the call site (injected via the `client` kwarg);
the GraphQL wire format itself is covered by tests/unit/adapters/.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.linear import CreatedIssue, LinearNetworkError
from app.config import get_settings
from app.models import HubIssue, Source, StatusHistory, Ticket, User
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


def test_push_skips_already_superseded(world: Session) -> None:
    # creator 毕业时已 hub-dedup 合并（superseded）→ linear_push 跳过不重复推
    orig = _make_hub(world, 50, linear_uuid="u", linear_identifier="ENG-50")
    hub = _make_hub(world, 51, superseded_by_hub_issue_id=orig.id)
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


def test_push_failure_marks_pending(world: Session) -> None:
    hub = _make_hub(world, 7)
    fake = _FakeLinearClient(raises=LinearNetworkError("timeout"))
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    world.refresh(hub)
    assert hub.linear_uuid is None  # 可重试
    assert hub.status == "pending"  # 待人工处理
    sh = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="pending")
        .one()
    )
    assert "Linear 推送失败" in (sh.reason or "")


def test_unmatched_individual_assignee_marks_pending_without_push(world: Session) -> None:
    """个人处理人（有邮箱）在 Linear 查无此人 → 不推送，置 pending 待人工。"""
    world.add(
        User(id=8, feishu_uid="ou_c", name="王五", email="wangwu@kingdee.com")
    )  # 有邮箱、无 linear_user_id
    world.commit()
    hub = _make_hub(world, 11, assigned_user_id=8)
    fake = _FakeLinearClient()
    assert push_hub_issue_to_linear(hub.id, world, client=fake) is None  # type: ignore[arg-type]
    assert fake.requests == []  # 根本没尝试推
    world.refresh(hub)
    assert hub.status == "pending"
    assert hub.linear_uuid is None
    sh = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="pending")
        .one()
    )
    assert "查无此人" in (sh.reason or "")
    assert "wangwu@kingdee.com" in (sh.reason or "")


def test_pending_not_duplicated_on_retry(world: Session) -> None:
    """重试仍失败时不重复写 pending history。"""
    world.add(User(id=9, feishu_uid="ou_d", name="赵六", email="zhaoliu@kingdee.com"))
    world.commit()
    hub = _make_hub(world, 12, assigned_user_id=9)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    rows = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="pending")
        .all()
    )
    assert len(rows) == 1


def test_repush_success_restores_pending_to_created(world: Session) -> None:
    """pending 后修复（同步上了 Linear）→ 重推成功自动恢复 created。"""
    world.add(User(id=10, feishu_uid="ou_e", name="孙七", email="sunqi@kingdee.com"))
    world.commit()
    hub = _make_hub(world, 13, assigned_user_id=10)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    world.refresh(hub)
    assert hub.status == "pending"

    # 人加入 Linear + sync 后映射补上
    world.query(User).filter_by(id=10).update(
        {"linear_user_id": "lin-u-10", "linear_team_id": "team-aralgo"}
    )
    world.commit()
    res = push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert res is not None
    world.refresh(hub)
    assert hub.status == "created"  # pending 解除
    assert hub.linear_identifier == "ENG-42"
    assert fake.requests[-1].team_id == "team-aralgo"  # type: ignore[attr-defined]
    recover = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="created")
        .one()
    )
    assert "pending 解除" in (recover.reason or "")


def test_group_assignee_still_degrades_not_pending(world: Session) -> None:
    """组账号（无邮箱）仍走优雅降级：推默认 team 无 assignee，不置 pending。"""
    world.add(User(id=11, feishu_uid="ou_grp2", name="费用报销组"))  # 无邮箱
    world.commit()
    hub = _make_hub(world, 14, assigned_user_id=11)
    fake = _FakeLinearClient()
    res = push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert res is not None
    world.refresh(hub)
    assert hub.status == "created"  # 不是 pending
    assert fake.requests[0].team_id == "team-1"  # type: ignore[attr-defined]
    assert fake.requests[0].assignee_id is None  # type: ignore[attr-defined]


def test_push_priority_default_zero(world: Session) -> None:
    hub = _make_hub(world, 8, priority=None)
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert fake.requests[0].priority == 0  # type: ignore[attr-defined]


def test_hub_dedup_supersede_skips_linear(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """hub-dedup 命中 → 当前 hub supersede 到已有 hub，不调 Linear。"""
    from app.services.hub_issues import linear_push as lp

    existing = _make_hub(world, 90)
    existing.linear_uuid = "u-existing"
    existing.linear_identifier = "CNPRD-100"
    world.commit()
    new = _make_hub(world, 91)

    # 直接桩掉 hub_dedup.find_duplicate_hub（其内部已另有单测）
    from app.services.hub_issues import hub_dedup

    monkeypatch.setattr(hub_dedup, "find_duplicate_hub", lambda db, hub, **kw: existing.id)

    fake = _FakeLinearClient()
    res = lp.push_hub_issue_to_linear(new.id, world, client=fake)  # type: ignore[arg-type]
    assert res is None
    assert fake.requests == []  # 没建 Linear
    world.refresh(new)
    world.refresh(existing)
    assert new.superseded_by_hub_issue_id == existing.id
    assert new.linear_uuid is None
    assert existing.occurrence_count == 2  # 已有 hub 次数 +1


def test_hub_dedup_no_dup_pushes_normally(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.hub_issues import hub_dedup
    from app.services.hub_issues import linear_push as lp

    monkeypatch.setattr(hub_dedup, "find_duplicate_hub", lambda db, hub, **kw: None)
    hub = _make_hub(world, 92)
    fake = _FakeLinearClient()
    res = lp.push_hub_issue_to_linear(hub.id, world, client=fake)  # type: ignore[arg-type]
    assert res is not None and len(fake.requests) == 1
