"""owner-split 测试（ADR-0016 P4）— 分解执行器守卫 + 进度通知 x/n 双 kind."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from adapters.linear import CreatedIssue, IssueState, LinearBusinessError
from app.config import get_settings
from app.models import (
    HubIssue,
    HubIssueLinearIssue,
    Source,
    StatusHistory,
    SyncOutbox,
    Ticket,
    User,
)
from app.services.hub_issues.linear_status_sync import sync_linear_statuses
from app.services.hub_issues.owner_split import (
    OwnerSplitError,
    SubTaskIn,
    execute_owner_split,
    notify_sub_issue_done,
)


class _FakeLinearClient:
    def __init__(self, *, fail_at: int | None = None):
        self.created: list = []  # CreateIssueRequest
        self._fail_at = fail_at
        self.states: list[IssueState] = []

    def create_issue(self, req):  # type: ignore[no-untyped-def]
        if self._fail_at is not None and len(self.created) + 1 >= self._fail_at:
            raise LinearBusinessError("boom")
        self.created.append(req)
        n = len(self.created)
        return CreatedIssue(
            id=f"sub-uuid-{n}", identifier=f"CNPRD-9{n}", url=f"https://linear/{n}", title=req.title
        )

    def get_issue_states(self, issue_ids):  # type: ignore[no-untyped-def]
        return self.states

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _linear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("LINEAR_TEAM_ID", "team-default")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(
        User(
            id=5,
            feishu_uid="ou_dev",
            name="dev-a",
            email="a@x.com",
            linear_user_id="lu-a",
            linear_team_id="team-a",
        )
    )
    db_session.add(User(id=6, feishu_uid="ou_dev2", name="dev-b"))  # 组账号：无邮箱
    db_session.add(
        HubIssue(
            id=80,
            short_code="HUB-000080",
            type="Demand",
            title="批量导出增强",
            status="in_progress",
            linear_uuid="parent-uuid",
            linear_identifier="CNPRD-80",
        )
    )
    db_session.flush()
    db_session.add(
        Ticket(
            id=400,
            short_code="TKT-000400",
            source_code="ksm",
            source_ticket_id="os-1",
            type="Raw",
            status="linked",
            title="要批量导出",
            hub_issue_id=80,
        )
    )
    db_session.commit()
    return db_session


def _two_tasks() -> list[SubTaskIn]:
    return [
        SubTaskIn(title="导出接口", assignee_user_id=5),
        SubTaskIn(title="前端下载页", assignee_user_id=6),
    ]


def test_execute_creates_sub_issues(world: Session) -> None:
    client = _FakeLinearClient()
    r = execute_owner_split(
        world,
        80,
        subtasks=_two_tasks(),
        executed_by="user:carol",
        client=client,  # type: ignore[arg-type]
    )
    assert len(r.sub_issues) == 2
    # parentId 挂主 issue；个人责任人落自己 team、组账号回落默认
    assert all(req.parent_id == "parent-uuid" for req in client.created)
    assert client.created[0].team_id == "team-a" and client.created[0].assignee_id == "lu-a"
    assert client.created[1].team_id == "team-default" and client.created[1].assignee_id is None
    rows = world.query(HubIssueLinearIssue).filter_by(hub_issue_id=80).all()
    assert [row.linear_identifier for row in rows] == ["CNPRD-91", "CNPRD-92"]
    audit = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=80)
        .order_by(StatusHistory.id.desc())
        .first()
    )
    assert "按责任人拆分" in (audit.reason or "")


def test_execute_guards(world: Session) -> None:
    client = _FakeLinearClient()
    with pytest.raises(OwnerSplitError, match="至少 2 个"):
        execute_owner_split(
            world, 80, subtasks=[SubTaskIn(title="只一个")], executed_by="u", client=client
        )  # type: ignore[arg-type]
    # 未推 Linear 的 hub 拒绝
    world.add(HubIssue(id=81, short_code="HUB-000081", type="Demand", title="x", status="created"))
    world.commit()
    with pytest.raises(OwnerSplitError, match="尚未推送 Linear"):
        execute_owner_split(world, 81, subtasks=_two_tasks(), executed_by="u", client=client)  # type: ignore[arg-type]
    # 非研发类拒绝
    world.add(
        HubIssue(id=82, short_code="HUB-000082", type="Operation", title="x", status="created")
    )
    world.commit()
    with pytest.raises(OwnerSplitError, match="仅限研发类"):
        execute_owner_split(world, 82, subtasks=_two_tasks(), executed_by="u", client=client)  # type: ignore[arg-type]


def test_execute_rejects_resplit(world: Session) -> None:
    client = _FakeLinearClient()
    execute_owner_split(world, 80, subtasks=_two_tasks(), executed_by="u", client=client)  # type: ignore[arg-type]
    with pytest.raises(OwnerSplitError, match="已拆分过"):
        execute_owner_split(world, 80, subtasks=_two_tasks(), executed_by="u", client=client)  # type: ignore[arg-type]


def test_execute_rejects_unknown_linear_individual(world: Session) -> None:
    world.add(User(id=7, feishu_uid="ou_x", name="new-guy", email="n@x.com"))  # 有邮箱无映射
    world.commit()
    with pytest.raises(OwnerSplitError, match="查无此人"):
        execute_owner_split(
            world,
            80,
            subtasks=[SubTaskIn(title="a", assignee_user_id=7), SubTaskIn(title="b")],
            executed_by="u",
            client=_FakeLinearClient(),  # type: ignore[arg-type]
        )


def test_execute_partial_failure_keeps_created(world: Session) -> None:
    client = _FakeLinearClient(fail_at=2)
    with pytest.raises(OwnerSplitError, match="第 2/2 个"):
        execute_owner_split(world, 80, subtasks=_two_tasks(), executed_by="u", client=client)  # type: ignore[arg-type]
    rows = world.query(HubIssueLinearIssue).filter_by(hub_issue_id=80).all()
    assert len(rows) == 1  # 已建的保留（Linear 侧已存在，不假装没发生）


# ---- 进度通知 ---------------------------------------------------------------


def _mk_subs(db: Session, n: int) -> list[HubIssueLinearIssue]:
    subs = []
    for i in range(1, n + 1):
        s = HubIssueLinearIssue(
            hub_issue_id=80,
            linear_uuid=f"sub-uuid-{i}",
            linear_identifier=f"CNPRD-9{i}",
            title=f"子任务{i}",
            created_by="user:carol",
        )
        db.add(s)
        subs.append(s)
    db.commit()
    return subs


def test_notify_progress_then_final(world: Session) -> None:
    subs = _mk_subs(world, 3)
    now = datetime.now(UTC)
    # 第 1 个完成 → progress_note（1/3，不关单）
    subs[0].released_at = now
    assert notify_sub_issue_done(world, subs[0]) == 1
    world.commit()
    row = world.query(SyncOutbox).filter_by(kind="progress_note").one()
    assert row.target_source_code == "ksm" and row.ticket_id == 400
    assert row.payload["progress"] == {"x": 1, "n": 3}
    assert "第 1 个" in row.payload["note"] and "剩余 2 个" in row.payload["note"]
    # 幂等：已通知不重复入队
    assert notify_sub_issue_done(world, subs[0]) == 0

    # 第 2、3 个完成 → 第 3 条是 release_note（关单）
    subs[1].released_at = now
    notify_sub_issue_done(world, subs[1])
    subs[2].released_at = now
    assert notify_sub_issue_done(world, subs[2]) == 1
    world.commit()
    final = world.query(SyncOutbox).filter_by(kind="release_note").one()
    assert "全部 3 个子任务已完成" in final.payload["note"]
    hub = world.get(HubIssue, 80)
    assert hub.release_notified_at is not None  # 防 devcollab 二次关单
    assert hub.feedback_status == "pending"


def test_notify_final_skipped_if_already_released(world: Session) -> None:
    """主管已手动发过发版通知 → x=n 不再发 release_note（防二次关单）。"""
    subs = _mk_subs(world, 2)
    hub = world.get(HubIssue, 80)
    hub.release_notified_at = datetime.now(UTC)
    now = datetime.now(UTC)
    subs[0].released_at = now
    notify_sub_issue_done(world, subs[0])  # 1/2 progress 正常发
    subs[1].released_at = now
    assert notify_sub_issue_done(world, subs[1]) == 0
    world.commit()
    assert world.query(SyncOutbox).filter_by(kind="release_note").count() == 0


# ---- 轮询联动（P4b）---------------------------------------------------------


def test_status_sync_completes_sub_and_enqueues(world: Session) -> None:
    _mk_subs(world, 2)
    client = _FakeLinearClient()
    client.states = [
        IssueState(
            id="parent-uuid", identifier="CNPRD-80", state_name="In Progress", state_type="started"
        ),
        IssueState(
            id="sub-uuid-1", identifier="CNPRD-91", state_name="Done", state_type="completed"
        ),
        IssueState(
            id="sub-uuid-2", identifier="CNPRD-92", state_name="In Progress", state_type="started"
        ),
    ]
    rep = sync_linear_statuses(world, client=client)  # type: ignore[arg-type]
    assert rep.sub_scanned == 2 and rep.sub_completed == 1 and rep.sub_outbox == 1
    sub1 = world.query(HubIssueLinearIssue).filter_by(linear_uuid="sub-uuid-1").one()
    assert sub1.released_at is not None and sub1.notified_at is not None
    assert sub1.status == "Done"
    sub2 = world.query(HubIssueLinearIssue).filter_by(linear_uuid="sub-uuid-2").one()
    assert sub2.released_at is None and sub2.status == "In Progress"
    assert world.query(SyncOutbox).filter_by(kind="progress_note").count() == 1
    # 已完成的子 issue 不再进下一轮扫描
    rep2 = sync_linear_statuses(world, client=client)  # type: ignore[arg-type]
    assert rep2.sub_scanned == 1
