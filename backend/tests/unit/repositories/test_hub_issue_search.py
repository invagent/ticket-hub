from app.models import HubIssue
from app.repositories.ticket import HubIssueRepository


def _mk_hub(db, *, short_code: str, title: str, type_: str = "Operation") -> HubIssue:
    h = HubIssue(short_code=short_code, type=type_, title=title, status="created")
    db.add(h)
    db.flush()
    return h


def test_search_matches_short_code(db_session):
    _mk_hub(db_session, short_code="HUB-000123", title="登录报错")
    _mk_hub(db_session, short_code="HUB-000999", title="导出失败")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search="000123")
    assert p.total == 1
    assert p.items[0].short_code == "HUB-000123"


def test_search_matches_title_case_insensitive(db_session):
    _mk_hub(db_session, short_code="HUB-000200", title="Login Error")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search="login")
    assert p.total == 1


def test_empty_search_returns_all(db_session):
    _mk_hub(db_session, short_code="HUB-000300", title="a")
    _mk_hub(db_session, short_code="HUB-000400", title="b")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search=None)
    assert p.total == 2
    p2 = repo.list_paginated(search="")
    assert p2.total == 2
