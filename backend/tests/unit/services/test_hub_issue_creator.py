"""hub_issue creator tests (D4) — graduation guards, linkage, idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    HubIssue,
    Source,
    StatusHistory,
    Ticket,
    TicketHubIssueHistory,
)
from app.services.hub_issues.creator import (
    HubIssueCreateError,
    ensure_hub_issue_for_ticket,
)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _make_ticket(db: Session, n: int, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"TKT-HUB-{n}",
        "source_code": "ksm",
        "source_ticket_id": f"hub-{n}",
        "type": "Raw",
        "status": "received",
        "title": f"开票失败 {n}",
        "body": "详细描述",
        "predicted_type": "Bug_fix",
        "assigned_user_id": None,
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_create_links_and_audits(world: Session) -> None:
    t = _make_ticket(world, 1)
    res = ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)
    assert res.created is True
    assert res.type == "Bug_fix"
    assert res.hub_issue_short_code == "HUB-000001"

    hub = world.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.type == "Bug_fix"
    assert hub.title == t.title
    assert hub.canonical_body == t.body
    assert hub.status == "created"

    world.refresh(t)
    assert t.hub_issue_id == hub.id
    link = world.query(TicketHubIssueHistory).filter_by(ticket_id=t.id).one()
    assert link.hub_issue_id == hub.id
    assert link.human_confirmed is True  # user: prefix
    sh = world.query(StatusHistory).filter_by(entity_type="hub_issue", entity_id=hub.id).one()
    assert sh.to_status == "created"


def test_complaint_not_auto_graduated(world: Session) -> None:
    """ADR-0016 P2a：投诉停 ticket 层，无 override 不自动毕业。"""
    t = _make_ticket(world, 20, predicted_type="Complaint")
    with pytest.raises(HubIssueCreateError, match="投诉"):
        ensure_hub_issue_for_ticket(t.id, created_by="agent:auto", db=world)


def test_complaint_can_be_converted_with_override(world: Session) -> None:
    """主管把投诉转成 Bug/Op/Demand 后可毕业（type_override 放行）。"""
    t = _make_ticket(world, 21, predicted_type="Complaint")
    res = ensure_hub_issue_for_ticket(
        t.id, created_by="user:boss", type_override="Bug_fix", db=world
    )
    assert res.created is True and res.type == "Bug_fix"


def test_create_idempotent_on_linked_ticket(world: Session) -> None:
    t = _make_ticket(world, 2)
    first = ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)
    again = ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)
    assert again.created is False
    assert again.hub_issue_id == first.hub_issue_id
    assert world.query(HubIssue).count() == 1


def test_type_override_beats_predicted(world: Session) -> None:
    t = _make_ticket(world, 3, predicted_type="Operation")
    res = ensure_hub_issue_for_ticket(
        t.id, created_by="user:boss", type_override="Demand", db=world
    )
    assert res.type == "Demand"


def test_auto_created_not_human_confirmed(world: Session) -> None:
    t = _make_ticket(world, 4)
    ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=world)
    link = world.query(TicketHubIssueHistory).filter_by(ticket_id=t.id).one()
    assert link.human_confirmed is False


def test_unclassified_without_override_rejected(world: Session) -> None:
    t = _make_ticket(world, 5, predicted_type=None)
    with pytest.raises(HubIssueCreateError, match="no valid type"):
        ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)


def test_split_parent_rejected(world: Session) -> None:
    t = _make_ticket(world, 6, type="Parent", status="split")
    with pytest.raises(HubIssueCreateError, match="split Parent"):
        ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)


def test_missing_ticket_rejected(world: Session) -> None:
    with pytest.raises(HubIssueCreateError, match="not found"):
        ensure_hub_issue_for_ticket(99999, created_by="user:boss", db=world)


def test_untitled_ticket_rejected(world: Session) -> None:
    t = _make_ticket(world, 7, title="")
    with pytest.raises(HubIssueCreateError, match="no title"):
        ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)


def test_short_codes_increment(world: Session) -> None:
    a = _make_ticket(world, 8)
    b = _make_ticket(world, 9)
    r1 = ensure_hub_issue_for_ticket(a.id, created_by="user:boss", db=world)
    r2 = ensure_hub_issue_for_ticket(b.id, created_by="user:boss", db=world)
    assert r1.hub_issue_short_code == "HUB-000001"
    assert r2.hub_issue_short_code == "HUB-000002"


# ---- hub-dedup 全类型：毕业时命中重复 → 挂原 hub ----


def test_graduate_merges_on_dedup_hit(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.hub_issues.creator as creator_mod

    orig = HubIssue(
        short_code="HUB-ORIG",
        type="Bug_fix",
        title="开票失败原始",
        status="created",
        product_line_code=None,
        occurrence_count=1,
    )
    world.add(orig)
    world.flush()
    t = _make_ticket(world, 20)
    # mock 查重命中 orig（并模拟 supersede 副作用）
    monkeypatch.setattr(creator_mod, "maybe_supersede_duplicate", lambda db, hub: orig.id)
    res = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=world)
    assert res.created is False
    assert res.hub_issue_id == orig.id
    world.refresh(t)
    assert t.hub_issue_id == orig.id  # ticket 挂原 hub


def test_graduate_creates_when_no_dup(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.hub_issues.creator as creator_mod

    t = _make_ticket(world, 21)
    monkeypatch.setattr(creator_mod, "maybe_supersede_duplicate", lambda db, hub: None)
    res = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=world)
    assert res.created is True


def test_dedup_merge_into_answered_hub_backfills_reply(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合并到已答复 Operation hub → 新 ticket 回填 cached_reply + 入 reply outbox。

    #2 修复：否则第二个客户收不到答复。
    """
    import app.services.hub_issues.creator as creator_mod
    from app.models import SyncOutbox

    orig = HubIssue(
        short_code="HUB-ANSWERED",
        type="Operation",
        title="开票失败",
        status="created",
        op_status="answered",
        op_handler="agent",
        reply_content="您好，请在【发票管理】重新发起开票。",
        reply_content_version=1,
        reply_authored_by="agent:ai_cs",
        occurrence_count=1,
    )
    world.add(orig)
    world.flush()
    t = _make_ticket(world, 30, predicted_type="Operation")
    monkeypatch.setattr(creator_mod, "maybe_supersede_duplicate", lambda db, hub: orig.id)
    res = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=world)
    assert res.created is False
    world.refresh(t)
    # 新 ticket 拿到答复缓存
    assert t.cached_reply_content == "您好，请在【发票管理】重新发起开票。"
    assert t.cached_reply_version == 1
    # 且入了 reply outbox（新工单源系统 KSM 会收到答复）
    ob = world.query(SyncOutbox).filter_by(ticket_id=t.id, kind="reply").first()
    assert ob is not None
    assert ob.source_ticket_id == t.source_ticket_id
    assert ob.payload["reply_content"] == "您好，请在【发票管理】重新发起开票。"


def test_dedup_merge_into_unanswered_hub_no_backfill(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合并到未答复 hub（reply_v=0）→ 不回填（没答复可发）。"""
    import app.services.hub_issues.creator as creator_mod
    from app.models import SyncOutbox

    orig = HubIssue(
        short_code="HUB-UNANSWERED",
        type="Operation",
        title="开票失败",
        status="created",
        op_status="processing",
        op_handler="agent",
        reply_content_version=0,
        occurrence_count=1,
    )
    world.add(orig)
    world.flush()
    t = _make_ticket(world, 31, predicted_type="Operation")
    monkeypatch.setattr(creator_mod, "maybe_supersede_duplicate", lambda db, hub: orig.id)
    ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=world)
    world.refresh(t)
    assert t.cached_reply_content is None
    assert world.query(SyncOutbox).filter_by(ticket_id=t.id, kind="reply").first() is None


def test_manual_graduate_skips_dedup(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """主管手动毕业（created_by=user:*）不跑 hub_dedup——人已判断，机器不该覆盖。

    即使查重会命中（mock 返回 orig.id），手动路径也应新建独立 hub，不被合并。
    """
    import app.services.hub_issues.creator as creator_mod

    orig = HubIssue(
        short_code="HUB-ORIG-M",
        type="Bug_fix",
        title="开票失败原始",
        status="created",
        occurrence_count=1,
    )
    world.add(orig)
    world.flush()
    t = _make_ticket(world, 22)
    called = {"dedup": 0}

    def _spy(db, hub):  # type: ignore[no-untyped-def]
        called["dedup"] += 1
        return orig.id  # 若被调用会命中合并

    monkeypatch.setattr(creator_mod, "maybe_supersede_duplicate", _spy)
    res = ensure_hub_issue_for_ticket(
        t.id, created_by="user:boss", type_override="Bug_fix", db=world
    )
    assert called["dedup"] == 0  # 手动路径根本不跑查重
    assert res.created is True
    assert res.hub_issue_id != orig.id  # 新建独立 hub，没被合并
    world.refresh(t)
    assert t.hub_issue_id == res.hub_issue_id


# ---- op_status 初始化：仅 Operation 毕业时设，研发类恒 NULL ----


def test_graduate_operation_inits_op_status(world: Session) -> None:
    """Operation 毕业 → op_status=processing, handler=agent。"""
    t = _make_ticket(world, 30, predicted_type="Operation")
    res = ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)
    hub = world.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"
    assert hub.op_status_changed_at is not None


def test_graduate_bugfix_no_op_status(world: Session) -> None:
    """研发类毕业 → op_status 恒 NULL。"""
    t = _make_ticket(world, 31, predicted_type="Bug_fix")
    res = ensure_hub_issue_for_ticket(t.id, created_by="user:boss", db=world)
    hub = world.get(HubIssue, res.hub_issue_id)
    assert hub is not None
    assert hub.op_status is None
    assert hub.op_handler is None
    assert hub.op_status_changed_at is None
