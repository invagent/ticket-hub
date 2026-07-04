"""研发协同动作 tests — 催办频率限制 / 发版通知 outbox / 自查登记 / 回访记录."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, SyncOutbox, Ticket
from app.services.hub_issues import devcollab as dc


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(
        HubIssue(
            id=1,
            short_code="HUB-000001",
            type="Bug_fix",
            title="导出超时",
            status="in_progress",
            linear_uuid="lin-uuid-1",
            linear_identifier="CNPRD-9",
            linear_status="In Progress",
        )
    )
    db_session.add(
        HubIssue(
            id=2,
            short_code="HUB-000002",
            type="Bug_fix",
            title="闪退",
            status="released",
            linear_uuid="lin-uuid-2",
            linear_identifier="CNPRD-10",
            linear_status="Done",
        )
    )
    db_session.add(
        Ticket(
            id=100,
            short_code="TKT-100",
            source_code="ksm",
            source_ticket_id="B-1",
            type="Raw",
            status="in_progress",
            title="闪退工单",
            hub_issue_id=2,
        )
    )
    db_session.commit()
    return db_session


def _mock_linear():  # type: ignore[no-untyped-def]
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.create_comment.return_value = "c-1"
    return client


def test_urge_posts_comment_and_counts(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    from app.config import get_settings

    get_settings.cache_clear()
    client = _mock_linear()
    with patch("adapters.linear.LinearClient", return_value=client):
        r = dc.urge_hub_issue(world, 1, urged_by="user:t")
    assert r.urge_count == 1
    hub = world.get(HubIssue, 1)
    assert hub.urge_count == 1 and hub.last_urged_at is not None
    body = client.create_comment.call_args[0][1]
    assert "催办" in body and "HUB-000001" in body


def test_urge_cooldown_rejected(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    from app.config import get_settings

    get_settings.cache_clear()
    hub = world.get(HubIssue, 1)
    hub.last_urged_at = datetime.now(UTC) - timedelta(hours=2)
    world.commit()
    with pytest.raises(dc.DevCollabError, match="24 小时"):
        dc.urge_hub_issue(world, 1, urged_by="user:t")


def test_urge_without_linear_key_rejected(world: Session) -> None:
    # conftest 清空了 LINEAR_API_KEY
    with pytest.raises(dc.DevCollabError, match="Linear 未接通"):
        dc.urge_hub_issue(world, 1, urged_by="user:t")


def test_urge_not_pushed_rejected(world: Session) -> None:
    hub = world.get(HubIssue, 1)
    hub.linear_uuid = None
    world.commit()
    with pytest.raises(dc.DevCollabError, match="尚未推送"):
        dc.urge_hub_issue(world, 1, urged_by="user:t")


def test_notify_release_enqueues_outbox(world: Session) -> None:
    r = dc.notify_release(world, 2, fix_version="v5.8.2", note="已修复请升级", notified_by="user:t")
    assert len(r.outbox_ids) == 1
    hub = world.get(HubIssue, 2)
    assert hub.release_notified_at is not None
    assert hub.fix_version == "v5.8.2"
    assert hub.feedback_status == "pending"
    row = world.get(SyncOutbox, r.outbox_ids[0])
    assert row.kind == "release_note" and row.payload["note"] == "已修复请升级"
    assert row.target_source_code == "ksm"


def test_notify_release_twice_rejected(world: Session) -> None:
    dc.notify_release(world, 2, fix_version="v1", note="n", notified_by="u")
    with pytest.raises(dc.DevCollabError, match="已发过"):
        dc.notify_release(world, 2, fix_version="v1", note="n", notified_by="u")


def test_notify_release_not_done_rejected(world: Session) -> None:
    # hub 1: in_progress + Linear In Progress → 不能发
    with pytest.raises(dc.DevCollabError, match="尚未完成"):
        dc.notify_release(world, 1, fix_version="v1", note="n", notified_by="u")


def test_register_self_bug_creates_standalone(world: Session) -> None:
    r = dc.register_self_bug(
        world,
        title="对账时区错误",
        product_line_code=None,
        module=None,
        impact_versions="v5.7.0~v5.8.1",
        fix_version="v5.8.2",
        released=True,
        registered_by="user:t",
    )
    hub = world.get(HubIssue, r.hub_issue_id)
    assert hub.self_found is True and hub.type == "Bug_fix"
    assert hub.status == "released" and hub.actual_released_at is not None
    assert hub.linear_uuid is None  # 不推 Linear


def test_self_bug_no_customer_notify(world: Session) -> None:
    r = dc.register_self_bug(
        world,
        title="内部 bug",
        product_line_code=None,
        module=None,
        impact_versions=None,
        fix_version=None,
        released=True,
        registered_by="u",
    )
    # 自查工单无有源关联 → 发版通知拒绝
    with pytest.raises(dc.DevCollabError, match="无有源关联"):
        dc.notify_release(world, r.hub_issue_id, fix_version="v1", note="n", notified_by="u")


def test_record_feedback_roundtrip(world: Session) -> None:
    dc.notify_release(world, 2, fix_version="v1", note="n", notified_by="u")
    r = dc.record_feedback(world, 2, status="stillbad", note="升级后仍复现", recorded_by="u")
    assert r.feedback_status == "stillbad"
    hub = world.get(HubIssue, 2)
    assert hub.feedback_note == "升级后仍复现" and hub.feedback_at is not None


def test_record_feedback_before_notify_rejected(world: Session) -> None:
    with pytest.raises(dc.DevCollabError, match="尚未发过发版通知"):
        dc.record_feedback(world, 1, status="resolved", note="", recorded_by="u")


def test_operation_type_rejected(world: Session) -> None:
    world.add(
        HubIssue(id=3, short_code="HUB-000003", type="Operation", title="op", status="created")
    )
    world.commit()
    with pytest.raises(dc.DevCollabError, match="仅限研发类"):
        dc.urge_hub_issue(world, 3, urged_by="u")
