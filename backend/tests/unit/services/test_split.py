"""Split executor tests (D3-D 闭环) — pure data operations, no LLM anywhere.

Covers: materialization (constraint-satisfying Child rows, inheritance,
re-routing), parent flip, idempotency guards, decision validation, revert
(incl. progress-guard refusal), and find_pending_split_decision.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AgentDecision,
    AssignmentScopeModule,
    ProductLine,
    Source,
    StatusHistory,
    Ticket,
    User,
)
from app.services.agents.split import (
    RevertSplitResult,
    SplitError,
    SplitResult,
    execute_split,
    execute_split_for_ticket,
    find_pending_split_decision,
    revert_split,
)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-fapiao", module="数电开票")
    )
    db_session.commit()
    return db_session


def _make_parent(db: Session, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-000042",
        "source_code": "ksm",
        "source_ticket_id": "split-src-1",
        "type": "Raw",
        "status": "received",
        "title": "1、开票步骤咨询 2、状态不同步",
        "body": "原始完整描述……",
        "product_line_code": "cloud-fapiao",
        "module": "数电开票",
        "reporter": {"name": "张三", "email": "z@x.com"},
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_decision(db: Session, ticket: Ticket, **proposal_overrides) -> AgentDecision:  # type: ignore[no-untyped-def]
    proposal = {
        "decision": "split",
        "confidence": 0.88,
        "reason": "两个独立问题",
        "sub_issues": [
            {"title": "红字确认单开票步骤咨询", "summary": "咨询正确操作流程"},
            {"title": "苍穹开票状态不同步", "summary": "税局已开票但系统显示未开票"},
        ],
        "model": "fake-model",
        "prompt_version": "v1",
    }
    proposal.update(proposal_overrides)
    d = AgentDecision(
        decision_type="split_ticket",
        subject_type="ticket",
        subject_id=ticket.id,
        proposal=proposal,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


# ---- execute ----------------------------------------------------------------


def test_execute_materializes_children(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)

    res = execute_split(decision.id, executed_by="user:boss", db=world)
    assert isinstance(res, SplitResult)
    assert len(res.child_ticket_ids) == 2

    world.refresh(parent)
    assert parent.type == "Parent"
    assert parent.status == "split"
    assert parent.children_ticket_ids == res.child_ticket_ids
    # 原文保留在 parent 上
    assert parent.body == "原始完整描述……"

    for i, cid in enumerate(res.child_ticket_ids, start=1):
        c = world.get(Ticket, cid)
        assert c is not None
        # ck_tickets_type_fields 契约
        assert c.type == "Child"
        assert c.source_code is None
        assert c.source_ticket_id is None
        assert c.internal_split_id == f"{parent.short_code}-C{i}"
        assert c.parent_ticket_id == parent.id
        # 继承
        assert c.product_line_code == "cloud-fapiao"
        assert c.module == "数电开票"
        assert c.reporter == {"name": "张三", "email": "z@x.com"}
        # 内容来自 sub_issue
        assert c.title in ("红字确认单开票步骤咨询", "苍穹开票状态不同步")
        # re-route: module 命中 alice
        assert c.assigned_user_id == 1
        assert c.status == "received"

    # 审计: decision.proposal 记录 materialized 块
    world.refresh(decision)
    m = decision.proposal["materialized"]
    assert m["by"] == "user:boss"
    assert m["child_ticket_ids"] == res.child_ticket_ids
    assert m["parent_prev_status"] == "received"

    # status_history: 2 child + 1 parent
    rows = world.query(StatusHistory).all()
    assert len(rows) == 3


def test_execute_idempotent_guard(world: Session) -> None:
    """同一 decision 第二次执行被 parent.type 卫语句拒绝。"""
    parent = _make_parent(world)
    decision = _make_decision(world, parent)
    execute_split(decision.id, executed_by="user:boss", db=world)
    with pytest.raises(SplitError, match="expected Raw"):
        execute_split(decision.id, executed_by="user:boss", db=world)
    # child 没有翻倍
    assert world.query(Ticket).filter_by(type="Child").count() == 2


def test_execute_rejects_reverted_decision(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)
    execute_split(decision.id, executed_by="user:boss", db=world)
    revert_split(decision.id, reverted_by="user:boss", db=world)
    with pytest.raises(SplitError, match="already reverted"):
        execute_split(decision.id, executed_by="user:boss", db=world)


def test_execute_rejects_wrong_decision_type(world: Session) -> None:
    parent = _make_parent(world)
    d = AgentDecision(
        decision_type="no_split",
        subject_type="ticket",
        subject_id=parent.id,
        proposal={"decision": "no_split", "sub_issues": []},
    )
    world.add(d)
    world.commit()
    with pytest.raises(SplitError, match="not split_ticket"):
        execute_split(d.id, executed_by="user:boss", db=world)


def test_execute_rejects_too_few_sub_issues(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent, sub_issues=[{"title": "只有一个", "summary": ""}])
    with pytest.raises(SplitError, match="<2 sub_issues"):
        execute_split(decision.id, executed_by="user:boss", db=world)


def test_execute_missing_decision(world: Session) -> None:
    with pytest.raises(SplitError, match="not found"):
        execute_split(999999, executed_by="user:boss", db=world)


def test_child_falls_to_default_pool_when_no_scope(world: Session) -> None:
    """模块无人认领时 child 不应硬挂 — 走 default_pool 或保持未分配。"""
    parent = _make_parent(
        world,
        short_code="TKT-000043",
        source_ticket_id="split-src-2",
        module="没人认领的模块",
    )
    decision = _make_decision(world, parent)
    res = execute_split(decision.id, executed_by="user:boss", db=world)
    for cid in res.child_ticket_ids:
        c = world.get(Ticket, cid)
        assert c is not None
        assert c.assigned_user_id is None  # 未配 default_pool → 未分配，等 reroute


# ---- auto path ---------------------------------------------------------------


def test_execute_for_ticket_happy(world: Session) -> None:
    parent = _make_parent(world)
    _make_decision(world, parent)
    res = execute_split_for_ticket(parent.id, executed_by="agent:split_auto", db=world)
    assert res is not None
    assert len(res.child_ticket_ids) == 2


def test_execute_for_ticket_no_decision_returns_none(world: Session) -> None:
    parent = _make_parent(world)
    assert execute_split_for_ticket(parent.id, executed_by="agent:split_auto", db=world) is None


def test_find_pending_skips_materialized(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)
    assert find_pending_split_decision(world, parent.id) is not None
    execute_split(decision.id, executed_by="user:boss", db=world)
    assert find_pending_split_decision(world, parent.id) is None  # parent 已非 Raw


# ---- revert ------------------------------------------------------------------


def test_revert_restores_parent_and_soft_deletes_children(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)
    res = execute_split(decision.id, executed_by="user:boss", db=world)

    out = revert_split(decision.id, reverted_by="user:boss", reason="拆错了", db=world)
    assert isinstance(out, RevertSplitResult)
    assert sorted(out.deleted_child_ids) == sorted(res.child_ticket_ids)

    world.refresh(parent)
    assert parent.type == "Raw"
    assert parent.status == "received"
    assert parent.children_ticket_ids is None

    for cid in res.child_ticket_ids:
        c = world.get(Ticket, cid)
        assert c is not None and c.deleted_at is not None

    world.refresh(decision)
    assert decision.status == "reverted"
    assert decision.reverted_by == "user:boss"
    assert decision.revert_reason == "拆错了"
    assert decision.reverted_at is not None


def test_revert_refused_when_child_in_progress(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)
    res = execute_split(decision.id, executed_by="user:boss", db=world)

    busy = world.get(Ticket, res.child_ticket_ids[0])
    assert busy is not None
    busy.status = "in_progress"
    world.commit()

    with pytest.raises(SplitError, match="in progress"):
        revert_split(decision.id, reverted_by="user:boss", db=world)
    # 全部保持原样 — 没有半截回滚
    world.refresh(parent)
    assert parent.type == "Parent"
    other = world.get(Ticket, res.child_ticket_ids[1])
    assert other is not None and other.deleted_at is None


def test_revert_requires_materialization(world: Session) -> None:
    parent = _make_parent(world)
    decision = _make_decision(world, parent)  # 从未 execute
    with pytest.raises(SplitError, match="never materialized"):
        revert_split(decision.id, reverted_by="user:boss", db=world)
