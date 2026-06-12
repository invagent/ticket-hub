"""Linear user sync tests (D4) — email match, team resolution, edge cases."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.linear import LinearTeam, LinearUser
from app.config import get_settings
from app.models import User
from app.services.linear.user_sync import sync_linear_users

# A default team id used as the multi-team tiebreak (= settings.linear_team_id).
_CNPRD = "team-cnprd-uuid"


class _FakeLinearClient:
    def __init__(self, users: list[LinearUser]) -> None:
        self._users = users

    def list_users(self) -> list[LinearUser]:
        return self._users

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _linear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("LINEAR_TEAM_ID", _CNPRD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _user(db: Session, n: int, **ov) -> User:  # type: ignore[no-untyped-def]
    base = {
        "feishu_uid": f"ou_{n}",
        "name": f"u{n}",
        "email": f"u{n}@kingdee.com",
        "role": "assignee",
    }
    base.update(ov)
    u = User(**base)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _lu(uid: str, email: str, teams: list[tuple[str, str]]) -> LinearUser:
    return LinearUser(
        id=uid,
        name=uid,
        email=email,
        active=True,
        teams=[LinearTeam(id=tid, key=key, name=key) for tid, key in teams],
    )


def test_match_single_team(db_session: Session) -> None:
    u = _user(db_session, 1, email="alice@kingdee.com")
    client = _FakeLinearClient([_lu("lin-1", "alice@kingdee.com", [("team-aralgo", "ARALGO")])])
    rep = sync_linear_users(db_session, client=client)
    assert rep.matched_count == 1
    db_session.refresh(u)
    assert u.linear_user_id == "lin-1"
    assert u.linear_team_id == "team-aralgo"


def test_match_is_case_insensitive(db_session: Session) -> None:
    u = _user(db_session, 1, email="Alice@Kingdee.com")
    client = _FakeLinearClient([_lu("lin-1", "alice@kingdee.com", [("t1", "CNPRD")])])
    sync_linear_users(db_session, client=client)
    db_session.refresh(u)
    assert u.linear_user_id == "lin-1"


def test_multi_team_prefers_default(db_session: Session) -> None:
    u = _user(db_session, 1, email="bob@kingdee.com")
    client = _FakeLinearClient(
        [_lu("lin-2", "bob@kingdee.com", [("team-aralgo", "ARALGO"), (_CNPRD, "CNPRD")])]
    )
    sync_linear_users(db_session, client=client)
    db_session.refresh(u)
    assert u.linear_team_id == _CNPRD  # default among the user's teams


def test_multi_team_without_default_is_null(db_session: Session) -> None:
    u = _user(db_session, 1, email="carol@kingdee.com")
    client = _FakeLinearClient(
        [_lu("lin-3", "carol@kingdee.com", [("team-aralgo", "ARALGO"), ("team-intprd", "INTPRD")])]
    )
    sync_linear_users(db_session, client=client)
    db_session.refresh(u)
    assert u.linear_user_id == "lin-3"
    assert u.linear_team_id is None  # ambiguous → fall back to default at push


def test_no_email_group_skipped(db_session: Session) -> None:
    grp = _user(db_session, 1, name="数电开票组", email=None)
    client = _FakeLinearClient([_lu("lin-x", "someone@kingdee.com", [("t1", "CNPRD")])])
    rep = sync_linear_users(db_session, client=client)
    assert rep.skipped_no_email == 1
    db_session.refresh(grp)
    assert grp.linear_user_id is None and grp.linear_team_id is None


def test_unmatched_local_counted(db_session: Session) -> None:
    _user(db_session, 1, email="nomatch@kingdee.com")
    client = _FakeLinearClient([_lu("lin-1", "other@kingdee.com", [("t1", "CNPRD")])])
    rep = sync_linear_users(db_session, client=client)
    assert rep.unmatched_local == 1
    assert rep.matched_count == 0
    assert "other@kingdee.com" in rep.unmatched_linear


def test_stale_mapping_cleared(db_session: Session) -> None:
    u = _user(db_session, 1, email="left@kingdee.com", linear_user_id="old", linear_team_id="old-t")
    client = _FakeLinearClient([_lu("lin-1", "still@kingdee.com", [("t1", "CNPRD")])])
    rep = sync_linear_users(db_session, client=client)
    assert rep.cleared_count == 1
    db_session.refresh(u)
    assert u.linear_user_id is None and u.linear_team_id is None


def test_inactive_and_emailless_linear_users_ignored(db_session: Session) -> None:
    u = _user(db_session, 1, email="dave@kingdee.com")
    bots = [
        LinearUser(id="bot", name="Bot", email="bot@oauthapp.linear.app", active=True, teams=[]),
        LinearUser(id="off", name="Off", email="dave@kingdee.com", active=False, teams=[]),
        _lu("lin-real", "dave@kingdee.com", [("t1", "CNPRD")]),
    ]
    sync_linear_users(db_session, client=_FakeLinearClient(bots))
    db_session.refresh(u)
    assert u.linear_user_id == "lin-real"  # the active, real-email match wins
