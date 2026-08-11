"""处理人(handler_user_id)流动：set_hub_tickets_handler helper。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket
from app.services.hub_issues.op_status import set_hub_tickets_handler


def test_set_hub_tickets_handler(db_session: Session) -> None:
    hub = HubIssue(
        id=600, short_code="HUB-000600", type="Operation", title="t", status="created"
    )
    db_session.add(hub)
    db_session.flush()
    db_session.add(
        Ticket(
            id=900,
            short_code="TKT-000900",
            source_code="ksm",
            source_ticket_id="k900",
            type="Raw",
            status="received",
            title="x",
            hub_issue_id=600,
            assigned_user_id=3,
            handler_user_id=3,
        )
    )
    db_session.commit()

    n = set_hub_tickets_handler(db_session, hub, 42)
    db_session.commit()
    assert n == 1
    got = db_session.get(Ticket, 900)
    assert got.handler_user_id == 42
    assert got.assigned_user_id == 3  # 责任人不变
