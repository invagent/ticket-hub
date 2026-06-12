"""Linear status back-sync tests (D4 第①段) — cascade mapping, idempotency,
canceled/missing handling, reopen."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.linear import IssueState, LinearNetworkError
from app.config import get_settings
from app.models import HubIssue, StatusHistory
from app.services.hub_issues.linear_status_sync import sync_linear_statuses


class _FakeLinearClient:
    def __init__(self, states: list[IssueState] | None = None, *, raises: Exception | None = None):
        self._states = states or []
        self._raises = raises
        self.queried_ids: list[str] = []

    def get_issue_states(self, issue_ids):  # type: ignore[no-untyped-def]
        self.queried_ids = list(issue_ids)
        if self._raises is not None:
            raise self._raises
        return self._states

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _linear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _hub(db: Session, n: int, **ov) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"HUB-LSS-{n}",
        "type": "Bug_fix",
        "title": f"问题 {n}",
        "status": "created",
        "linear_uuid": f"uuid-{n}",
        "linear_identifier": f"CNPRD-{n}",
    }
    base.update(ov)
    h = HubIssue(**base)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _state(n: int, name: str, type_: str) -> IssueState:
    return IssueState(id=f"uuid-{n}", identifier=f"CNPRD-{n}", state_name=name, state_type=type_)


def test_started_cascades_to_in_progress(db_session: Session) -> None:
    hub = _hub(db_session, 1)
    rep = sync_linear_statuses(
        db_session, client=_FakeLinearClient([_state(1, "In Progress", "started")])
    )  # type: ignore[arg-type]
    assert rep.status_changed == 1
    db_session.refresh(hub)
    assert hub.status == "in_progress"
    assert hub.linear_status == "In Progress"
    assert hub.linear_status_synced_at is not None
    sh = (
        db_session.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="in_progress")
        .one()
    )
    assert "CNPRD-1" in (sh.reason or "")


def test_completed_cascades_to_released_with_timestamp(db_session: Session) -> None:
    hub = _hub(db_session, 2, status="in_progress")
    sync_linear_statuses(db_session, client=_FakeLinearClient([_state(2, "Done", "completed")]))  # type: ignore[arg-type]
    db_session.refresh(hub)
    assert hub.status == "released"
    assert hub.actual_released_at is not None
    assert hub.linear_status == "Done"


def test_canceled_records_display_only(db_session: Session) -> None:
    """canceled 只镜像 linear_status，不动 hub 状态 — 留主管判断。"""
    hub = _hub(db_session, 3, status="in_progress")
    rep = sync_linear_statuses(
        db_session, client=_FakeLinearClient([_state(3, "Canceled", "canceled")])
    )  # type: ignore[arg-type]
    assert rep.status_changed == 0
    assert rep.linear_status_refreshed == 1
    db_session.refresh(hub)
    assert hub.status == "in_progress"  # 未变
    assert hub.linear_status == "Canceled"  # 但可见


def test_backlog_records_display_only(db_session: Session) -> None:
    hub = _hub(db_session, 4)
    sync_linear_statuses(db_session, client=_FakeLinearClient([_state(4, "Backlog", "backlog")]))  # type: ignore[arg-type]
    db_session.refresh(hub)
    assert hub.status == "created"
    assert hub.linear_status == "Backlog"


def test_idempotent_no_duplicate_history(db_session: Session) -> None:
    hub = _hub(db_session, 5)
    client = _FakeLinearClient([_state(5, "In Progress", "started")])
    sync_linear_statuses(db_session, client=client)  # type: ignore[arg-type]
    sync_linear_statuses(db_session, client=client)  # type: ignore[arg-type]
    rows = (
        db_session.query(StatusHistory).filter_by(entity_type="hub_issue", entity_id=hub.id).all()
    )
    assert len(rows) == 1  # 第二轮无变化不写


def test_reopen_released_back_to_in_progress(db_session: Session) -> None:
    """Linear 把 Done 的 issue 拖回 started → hub 跟随（Linear 是研发态源头）。"""
    hub = _hub(db_session, 6, status="released", linear_status="Done")
    sync_linear_statuses(
        db_session, client=_FakeLinearClient([_state(6, "In Progress", "started")])
    )  # type: ignore[arg-type]
    db_session.refresh(hub)
    assert hub.status == "in_progress"


def test_missing_in_linear_untouched(db_session: Session) -> None:
    hub = _hub(db_session, 7, linear_status="Todo")
    rep = sync_linear_statuses(db_session, client=_FakeLinearClient([]))  # type: ignore[arg-type]
    assert rep.missing_in_linear == 1
    db_session.refresh(hub)
    assert hub.status == "created" and hub.linear_status == "Todo"


def test_unpushed_hubs_not_scanned(db_session: Session) -> None:
    _hub(db_session, 8, linear_uuid=None, linear_identifier=None)
    client = _FakeLinearClient([])
    rep = sync_linear_statuses(db_session, client=client)  # type: ignore[arg-type]
    assert rep.scanned == 0
    assert client.queried_ids == []


def test_linear_error_sets_failed_not_raises(db_session: Session) -> None:
    _hub(db_session, 9)
    rep = sync_linear_statuses(
        db_session, client=_FakeLinearClient(raises=LinearNetworkError("timeout"))
    )  # type: ignore[arg-type]
    assert rep.failed is True
    assert rep.status_changed == 0


def test_completed_cascades_to_linked_tickets(db_session: Session) -> None:
    """决策 14 联动：Linear Done → hub released → 源工单 released + outbox。"""
    from app.models import Source, SyncOutbox, Ticket

    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    hub = _hub(db_session, 11, status="in_progress")
    t = Ticket(
        short_code="TKT-LSS-11",
        source_code="ksm",
        source_ticket_id="lss-11",
        type="Raw",
        status="in_progress",
        title="x",
        hub_issue_id=hub.id,
    )
    db_session.add(t)
    db_session.commit()

    sync_linear_statuses(db_session, client=_FakeLinearClient([_state(11, "Done", "completed")]))  # type: ignore[arg-type]
    db_session.refresh(t)
    assert t.status == "released"
    outbox = db_session.query(SyncOutbox).filter_by(kind="status", ticket_id=t.id).one()
    assert outbox.payload["to_status"] == "released"
    assert outbox.status == "pending"


def test_no_key_skips(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "")
    get_settings.cache_clear()
    _hub(db_session, 10)
    rep = sync_linear_statuses(db_session, client=_FakeLinearClient([]))  # type: ignore[arg-type]
    assert rep.scanned == 0
    get_settings.cache_clear()
