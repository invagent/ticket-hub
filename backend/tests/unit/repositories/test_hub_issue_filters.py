"""HubIssueRepository.list_paginated 新筛选（task_state / dev_stage / 时间区间）
+ filter_counts 聚合的单测。"""

from datetime import UTC, datetime

from app.models import HubIssue
from app.repositories.ticket import HubIssueRepository


def _mk(db, *, short_code, status="created", linear_status=None, first_seen=None, type_="Bug_fix"):
    h = HubIssue(
        short_code=short_code,
        type=type_,
        title=short_code,
        status=status,
        linear_status=linear_status,
    )
    if first_seen is not None:
        h.first_seen_at = first_seen
    db.add(h)
    db.flush()
    return h


def test_task_state_in_progress(db_session):
    _mk(db_session, short_code="HUB-1", status="in_progress")
    _mk(db_session, short_code="HUB-2", status="created")
    _mk(db_session, short_code="HUB-3", status="done")
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(task_state="in_progress")
    # created + in_progress 都归 in_progress 组
    assert {h.short_code for h in p.items} == {"HUB-1", "HUB-2"}
    assert p.total == 2


def test_task_state_done(db_session):
    _mk(db_session, short_code="HUB-1", status="in_progress")
    _mk(db_session, short_code="HUB-2", status="released")
    _mk(db_session, short_code="HUB-3", status="closed")
    _mk(db_session, short_code="HUB-4", status="resolved")  # resolved 也归已完成
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(task_state="done")
    assert {h.short_code for h in p.items} == {"HUB-2", "HUB-3", "HUB-4"}


def test_dev_stage_matches_linear_status_case_insensitive(db_session):
    _mk(db_session, short_code="HUB-1", linear_status="In Progress")
    _mk(db_session, short_code="HUB-2", linear_status="Done")
    _mk(db_session, short_code="HUB-3", linear_status="Backlog")
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(dev_stage="开发中")  # → in progress/started/...
    assert {h.short_code for h in p.items} == {"HUB-1"}
    p2 = repo.list_paginated(dev_stage="已发版")  # → done/completed/released
    assert {h.short_code for h in p2.items} == {"HUB-2"}


def test_created_time_range(db_session):
    _mk(db_session, short_code="HUB-OLD", first_seen=datetime(2026, 1, 1, tzinfo=UTC))
    _mk(db_session, short_code="HUB-NEW", first_seen=datetime(2026, 8, 1, tzinfo=UTC))
    repo = HubIssueRepository(db_session)
    p = repo.list_paginated(
        created_from=datetime(2026, 7, 1, tzinfo=UTC),
        created_to=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert {h.short_code for h in p.items} == {"HUB-NEW"}


def test_filter_counts_aggregates_all_pages(db_session):
    # 3 in_progress-ish + 1 done；dev_stage 各异
    _mk(db_session, short_code="HUB-1", status="in_progress", linear_status="In Progress")
    _mk(db_session, short_code="HUB-2", status="created", linear_status="Backlog")
    _mk(db_session, short_code="HUB-3", status="released", linear_status="Done")
    repo = HubIssueRepository(db_session)
    counts = repo.filter_counts()
    assert counts["task_state"]["all"] == 3
    assert counts["task_state"]["in_progress"] == 2  # in_progress + created
    assert counts["task_state"]["done"] == 1  # released
    assert counts["dev_stage"]["开发中"] == 1
    assert counts["dev_stage"]["待处理"] == 1  # backlog
    assert counts["dev_stage"]["已发版"] == 1  # done


def test_filter_counts_task_state_excludes_own_filter(db_session):
    # 选中 task_state=in_progress 时，done 档计数仍反映全集（排除 task_state 自身）
    _mk(db_session, short_code="HUB-1", status="in_progress")
    _mk(db_session, short_code="HUB-2", status="done")
    repo = HubIssueRepository(db_session)
    counts = repo.filter_counts(task_state="in_progress")
    assert counts["task_state"]["done"] == 1  # 未被 task_state 自身筛掉
    assert counts["task_state"]["in_progress"] == 1
