"""Tests for TicketRepository status filtering (SSOT on Ticket.status)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket
from app.repositories.ticket import TicketRepository


def test_list_paginated_excludes_closed_and_returned_from_processing(db_session: Session) -> None:
    """处理中筛选：坚决排除已关闭与转单退回工单，即使其关联 Hub 状态未流转。"""
    repo = TicketRepository(db_session)

    # 1. 正常处理中工单
    t_proc = Ticket(
        short_code="TKT-PROC-1",
        source_code="ksm",
        source_ticket_id="src-proc-1",
        type="Raw",
        status="processing",
        title="正在处理中",
    )
    db_session.add(t_proc)

    # 2. 转单退回工单，但其挂载的需求 Hub 任务 status 为 returned
    h_ret = HubIssue(
        short_code="HUB-DEMAND-RET",
        type="Demand",
        title="需求任务",
        status="returned",
    )
    db_session.add(h_ret)
    db_session.flush()

    t_ret = Ticket(
        short_code="TKT-RET-1",
        source_code="ksm",
        source_ticket_id="src-ret-1",
        type="Raw",
        status="transferred_return",
        title="转单退回工单",
        hub_issue_id=h_ret.id,
    )
    db_session.add(t_ret)

    # 3. 已关闭工单，但关联 Hub 残留 op_status 为 processing
    h_stale = HubIssue(
        short_code="HUB-OP-STALE",
        type="Operation",
        title="运营任务",
        status="draft",
        op_status="processing",  # 脏数据残留
    )
    db_session.add(h_stale)
    db_session.flush()

    t_closed = Ticket(
        short_code="TKT-CLOSED-1",
        source_code="ksm",
        source_ticket_id="src-closed-1",
        type="Raw",
        status="closed",
        title="已关闭工单",
        hub_issue_id=h_stale.id,
    )
    db_session.add(t_closed)
    db_session.commit()

    # 筛选：处理中 / 待审核 / 补充资料
    page = repo.list_paginated(op_statuses=["processing", "reviewing", "supplementing"])
    codes = [item.short_code for item in page.items]

    assert "TKT-PROC-1" in codes
    assert "TKT-RET-1" not in codes
    assert "TKT-CLOSED-1" not in codes

    # 筛选：转单退回
    page_ret = repo.list_paginated(op_statuses=["transferred_return"])
    codes_ret = [item.short_code for item in page_ret.items]
    assert "TKT-RET-1" in codes_ret
    assert "TKT-PROC-1" not in codes_ret
    assert "TKT-CLOSED-1" not in codes_ret

    # 筛选：已关闭
    page_closed = repo.list_paginated(op_statuses=["closed"])
    codes_closed = [item.short_code for item in page_closed.items]
    assert "TKT-CLOSED-1" in codes_closed
    assert "TKT-PROC-1" not in codes_closed
    assert "TKT-RET-1" not in codes_closed
