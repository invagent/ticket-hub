"""Ticket.handler_user_id 处理人字段（迁移 0030）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Ticket


def test_ticket_has_handler_user_id(db_session: Session) -> None:
    t = Ticket(
        short_code="TKT-H1",
        source_code="ksm",
        source_ticket_id="h1",
        type="Raw",
        status="received",
        title="x",
        assigned_user_id=None,
        handler_user_id=7,
    )
    db_session.add(t)
    db_session.commit()
    got = db_session.query(Ticket).filter_by(short_code="TKT-H1").one()
    assert got.handler_user_id == 7
