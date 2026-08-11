"""record_ticket_action：把 hub 维度操作投影为关联 ticket 的时间轴审计行。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory, Ticket
from app.services.hub_issues.op_status import record_ticket_action


def test_record_ticket_action_writes_row_per_linked_ticket(db_session: Session) -> None:
    hub = HubIssue(
        id=200,
        short_code="HUB-000200",
        type="Operation",
        title="t",
        status="created",
        op_status="answered",
    )
    db_session.add(hub)
    db_session.flush()
    db_session.add(
        Ticket(
            id=500,
            short_code="TKT-000500",
            source_code="ksm",
            source_ticket_id="k-500",
            type="Raw",
            status="in_progress",
            title="x",
            hub_issue_id=200,
        )
    )
    db_session.add(
        Ticket(
            id=501,
            short_code="TKT-000501",
            source_code="ksm",
            source_ticket_id="k-501",
            type="Raw",
            status="linked",
            title="y",
            hub_issue_id=200,
        )
    )
    db_session.commit()

    n = record_ticket_action(
        db_session, hub, action="reply", changed_by="user:张三", reason="已答复客户"
    )
    db_session.commit()

    assert n == 2
    rows = (
        db_session.query(StatusHistory)
        .filter(StatusHistory.entity_type == "ticket", StatusHistory.entity_id == 500)
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    # 纯操作事件：不改状态
    assert row.from_status == "in_progress"
    assert row.to_status == "in_progress"
    assert row.reason == "已答复客户"
    assert row.changed_by == "user:张三"
    assert row.metadata_ == {"action": "reply"}


def test_record_ticket_action_skips_deleted_tickets(db_session: Session) -> None:
    from datetime import UTC, datetime

    hub = HubIssue(
        id=201, short_code="HUB-000201", type="Operation", title="t", status="created"
    )
    db_session.add(hub)
    db_session.flush()
    db_session.add(
        Ticket(
            id=510,
            short_code="TKT-000510",
            source_code="ksm",
            source_ticket_id="k-510",
            type="Raw",
            status="received",
            title="live",
            hub_issue_id=201,
        )
    )
    db_session.add(
        Ticket(
            id=511,
            short_code="TKT-000511",
            source_code="ksm",
            source_ticket_id="k-511",
            type="Raw",
            status="received",
            title="dead",
            hub_issue_id=201,
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    n = record_ticket_action(
        db_session, hub, action="confirm_classification", changed_by="user:李四", reason="确认分类"
    )
    db_session.commit()
    assert n == 1  # 删除的 ticket 不写
