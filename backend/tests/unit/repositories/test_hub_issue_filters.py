"""HubIssueRepository 筛选（op_status / dev_stage 精确匹配 / 时间区间）
+ distinct_linear_statuses + filter_counts 聚合的单测。"""

from datetime import UTC, datetime

from app.models import HubIssue
from app.repositories.ticket import HubIssueRepository


def _mk(
    db,
    *,
    short_code,
    status="created",
    op_status=None,
    linear_status=None,
    first_seen=None,
    type_="Bug_fix",
):
    h = HubIssue(
        short_code=short_code,
        type=type_,
        title=short_code,
        status=status,
        op_status=op_status,
        linear_status=linear_status,
    )
    if first_seen is not None:
        h.first_seen_at = first_seen
    db.add(h)
    db.flush()
    return h


def test_op_status_filter(db_session):
    _mk(db_session, short_code="HUB-1", type_="Operation", op_status="answered")
    _mk(db_session, short_code="HUB-2", type_="Operation", op_status="processing")
    _mk(db_session, short_code="HUB-3", type_="Bug_fix")  # 研发单无 op_status
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(op_status="answered")
    assert {h.short_code for h in p.items} == {"HUB-1"}


def test_dev_stage_exact_match_linear_status(db_session):
    _mk(db_session, short_code="HUB-1", linear_status="In Progress")
    _mk(db_session, short_code="HUB-2", linear_status="Code Review")
    _mk(db_session, short_code="HUB-3", linear_status="Backlog")
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(dev_stage="In Progress")  # 精确匹配实际值
    assert {h.short_code for h in p.items} == {"HUB-1"}


def test_created_time_range(db_session):
    _mk(db_session, short_code="HUB-OLD", first_seen=datetime(2026, 1, 1, tzinfo=UTC))
    _mk(db_session, short_code="HUB-NEW", first_seen=datetime(2026, 8, 1, tzinfo=UTC))
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(
        created_from=datetime(2026, 7, 1, tzinfo=UTC),
        created_to=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert {h.short_code for h in p.items} == {"HUB-NEW"}


def test_distinct_linear_statuses(db_session):
    _mk(db_session, short_code="HUB-1", linear_status="In Progress")
    _mk(db_session, short_code="HUB-2", linear_status="In Progress")
    _mk(db_session, short_code="HUB-3", linear_status="Backlog")
    _mk(db_session, short_code="HUB-4", linear_status=None)  # 空不计入
    repo = HubIssueRepository(db_session)
    got = repo.distinct_linear_statuses()
    assert got[0] == "In Progress"  # 按数量降序，In Progress 2 条居首
    assert set(got) == {"In Progress", "Backlog"}


def test_filter_counts_op_status_and_dev_stage(db_session):
    _mk(db_session, short_code="HUB-1", type_="Operation", op_status="answered")
    _mk(db_session, short_code="HUB-2", type_="Operation", op_status="answered")
    _mk(db_session, short_code="HUB-3", type_="Operation", op_status="processing")
    _mk(db_session, short_code="HUB-4", linear_status="In Progress")
    _mk(db_session, short_code="HUB-5", linear_status="Backlog")
    repo = HubIssueRepository(db_session)
    counts = repo.filter_counts()
    assert counts["op_status"]["all"] == 5
    assert counts["op_status"]["answered"] == 2
    assert counts["op_status"]["processing"] == 1
    assert counts["dev_stage"]["In Progress"] == 1
    assert counts["dev_stage"]["Backlog"] == 1


def test_filter_counts_op_status_excludes_own_filter(db_session):
    # 选中 op_status=answered 时，processing 档计数仍反映全集（排除 op_status 自身）
    _mk(db_session, short_code="HUB-1", type_="Operation", op_status="answered")
    _mk(db_session, short_code="HUB-2", type_="Operation", op_status="processing")
    repo = HubIssueRepository(db_session)
    counts = repo.filter_counts(op_status="answered")
    assert counts["op_status"]["processing"] == 1
    assert counts["op_status"]["answered"] == 1
