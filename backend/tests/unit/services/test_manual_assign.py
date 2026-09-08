"""ManualAssignService unit tests (supervisor manual-assign, bypasses Router)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source, StatusHistory, Ticket, User
from app.services.supervisor.manual_assign import (
    AssignRequest,
    ManualAssignService,
    TargetUserInvalidError,
)


def _mk_user(db: Session, *, name: str, role: str, is_active: bool = True) -> User:
    u = User(feishu_uid=f"fs-{name}", name=name, role=role, is_active=is_active)
    db.add(u)
    db.flush()
    return u


def _mk_ticket(db: Session, *, short_code: str, assigned_user_id: int | None = None) -> Ticket:
    t = Ticket(
        type="Raw",
        source_code="ksm",
        source_ticket_id=f"src-{short_code}",
        short_code=short_code,
        title="t",
        body="b",
        status="received",
        assigned_user_id=assigned_user_id,
    )
    db.add(t)
    db.flush()
    return t


def _status_rows(db: Session, ticket_id: int) -> list[StatusHistory]:
    return list(
        db.execute(
            select(StatusHistory).where(
                StatusHistory.entity_type == "ticket",
                StatusHistory.entity_id == ticket_id,
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture(autouse=True)
def _seed_source(db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.flush()


def test_assign_single_success(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-1")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    assert res.assigned_count == 1
    assert res.not_found_count == 0
    assert res.results[0].success is True
    assert res.results[0].prev_assigned_user_id is None
    db_session.flush()
    # 转交改的是处理人（handler_user_id），责任人（assigned_user_id）不动
    assert db_session.get(Ticket, t.id).handler_user_id == target.id


def test_assign_records_status_history(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="admin")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-2")

    ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    rows = _status_rows(db_session, t.id)
    assert any(r.changed_by == "system:manual_assign" for r in rows)


def test_assign_target_member_allowed(db_session: Session) -> None:
    """处理人可以是 member 角色（真实处理人主体），不再按角色白名单拒绝。"""
    op = _mk_user(db_session, name="op", role="supervisor")
    member = _mk_user(db_session, name="m", role="member")
    t = _mk_ticket(db_session, short_code="T-3")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=member.id, operator_user_id=op.id)
    )
    assert res.assigned_count == 1
    db_session.flush()
    assert db_session.get(Ticket, t.id).handler_user_id == member.id


def test_assign_target_inactive(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    dead = _mk_user(db_session, name="x", role="assignee", is_active=False)
    t = _mk_ticket(db_session, short_code="T-4")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=dead.id, operator_user_id=op.id)
        )


def test_assign_target_not_found(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    t = _mk_ticket(db_session, short_code="T-5")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=99999, operator_user_id=op.id)
        )


def test_assign_partial_ticket_not_found(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-6")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id, 88888], assigned_user_id=target.id, operator_user_id=op.id)
    )
    assert res.assigned_count == 1
    assert res.not_found_count == 1
    by_id = {r.ticket_id: r for r in res.results}
    assert by_id[t.id].success is True
    assert by_id[88888].success is False


def test_assign_updates_hub_and_draft_subtasks(db_session: Session) -> None:
    from app.models import HubIssue

    op = _mk_user(db_session, name="op_sup", role="supervisor")
    target = _mk_user(db_session, name="new_handler", role="member")
    old_handler = _mk_user(db_session, name="old_handler", role="member")

    hub = HubIssue(
        short_code="HUB-T-7",
        type="Operation",
        title="测试主Hub",
        status="created",
        op_handler_user_id=old_handler.id,
        op_handler=f"user:{old_handler.name}",
    )
    db_session.add(hub)
    db_session.flush()

    t = _mk_ticket(db_session, short_code="T-7")
    t.hub_issue_id = hub.id
    t.handler_user_id = old_handler.id

    draft_sub = HubIssue(
        short_code="HUB-SUB-1",
        ticket_id=t.id,
        type="Operation",
        title="草稿子任务",
        status="draft",
        assigned_user_id=old_handler.id,
    )
    pushed_sub = HubIssue(
        short_code="HUB-SUB-2",
        ticket_id=t.id,
        type="Bug_fix",
        title="处理中子任务",
        status="processing",
        assigned_user_id=old_handler.id,
    )
    db_session.add_all([draft_sub, pushed_sub])
    db_session.flush()

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )
    assert res.assigned_count == 1

    db_session.refresh(t)
    db_session.refresh(hub)
    db_session.refresh(draft_sub)
    db_session.refresh(pushed_sub)

    assert t.handler_user_id == target.id
    # 主 Hub 的 op_handler_user_id 联动更新
    assert hub.op_handler_user_id == target.id
    assert hub.op_handler == f"user:{target.name}"
    # 草稿态子任务处理人联动更新
    assert draft_sub.assigned_user_id == target.id
    # 已进入处理中的子任务（例如已推 Linear）不随意覆盖
    assert pushed_sub.assigned_user_id == old_handler.id


def test_assign_member_permissions(db_session: Session) -> None:
    member_alice = _mk_user(db_session, name="alice", role="member")
    member_bob = _mk_user(db_session, name="bob", role="member")
    target = _mk_user(db_session, name="target_charlie", role="member")

    t_mine = _mk_ticket(db_session, short_code="T-8")
    t_mine.handler_user_id = member_alice.id

    t_other = _mk_ticket(db_session, short_code="T-9")
    t_other.handler_user_id = member_bob.id
    db_session.flush()

    # Alice 移交自己的工单 -> 成功
    res1 = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t_mine.id], assigned_user_id=target.id, operator_user_id=member_alice.id)
    )
    assert res1.assigned_count == 1
    assert res1.results[0].success is True

    # Alice 试图移交 Bob 的工单 -> 权限拦截失败
    res2 = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t_other.id], assigned_user_id=target.id, operator_user_id=member_alice.id)
    )
    assert res2.assigned_count == 0
    assert res2.results[0].success is False
    assert "无权转交" in res2.results[0].message

