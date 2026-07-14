"""智齿出站 writeback sender 单测。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from adapters.zhichi.types import Agent
from app.models import HubIssue, Source, SyncOutbox, Ticket
from app.services.zhichi.writeback import drain_zhichi_outbox


class _FakeClient:
    """替身：记录 reply_ticket 调用，get_agent_by_name 查不到返回 None。"""

    def __init__(self) -> None:
        self.replies: list = []

    def get_agent_by_name(self, name: str) -> Agent | None:
        if name == "查无此人":
            return None
        return Agent(agentid="agent-" + name, agent_name=name)

    def reply_ticket(self, req):  # type: ignore[no-untyped-def]
        self.replies.append(req)
        return {"ret_code": "000000"}

    def close(self) -> None:
        pass


@dataclass
class _Settings:
    zhichi_writeback_enabled: bool = True
    zhichi_writeback_dry_run: bool = False
    zhichi_writeback_batch: int = 20
    zhichi_writeback_max_attempts: int = 5
    zhichi_fallback_agent_name: str = "莉莉"
    zhichi_appid: str = "x"
    zhichi_app_key: str = "y"
    zhichi_base_url: str = "https://www.soboten.com"


def _seed(db: Session, *, deal_agent_name: str = "莉莉", kind: str = "reply", payload=None):  # type: ignore[no-untyped-def]
    if db.query(Source).filter_by(code="zhichi").first() is None:
        db.add(Source(code="zhichi", name="智齿"))
    t = Ticket(
        short_code="TKT-Z1",
        source_code="zhichi",
        source_ticket_id="ZT1",
        type="Raw",
        status="received",
        source_payload={
            "raw": {
                "ticket_title": "标题",
                "ticket_content": "正文",
                "ticket_level": 2,
                "deal_agent_name": deal_agent_name,
            }
        },
    )
    db.add(t)
    db.flush()
    hub = HubIssue(
        short_code="HUB-Z1",
        type="Operation",
        title="标题",
        status="created",
        reply_content="hub级答复",
    )
    db.add(hub)
    db.flush()
    ob = SyncOutbox(
        kind=kind,
        target_source_code="zhichi",
        ticket_id=t.id,
        source_ticket_id="ZT1",
        hub_issue_id=hub.id,
        payload=payload if payload is not None else {"reply_content": "这是回复"},
        status="pending",
    )
    db.add(ob)
    db.flush()
    return t, hub, ob


def test_drain_reply_calls_save(db_session: Session) -> None:
    _seed(db_session)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "3"  # reply → 已解决/关单
    assert fake.replies[0].reply_agentid == "agent-莉莉"
    assert fake.replies[0].reply_content == "这是回复"


def test_drain_dry_run_skips(db_session: Session) -> None:
    _seed(db_session)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(
        db_session, client=fake, settings=_Settings(zhichi_writeback_dry_run=True)
    )
    assert report.skipped == 1
    assert report.sent == 0
    assert fake.replies == []


def test_drain_fallback_agent_when_empty(db_session: Session) -> None:
    _seed(db_session, deal_agent_name="")
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].reply_agent_name == "莉莉"


def test_drain_agent_not_found_fails(db_session: Session) -> None:
    _seed(db_session, deal_agent_name="查无此人")
    db_session.commit()
    fake = _FakeClient()
    # max_attempts=1 → 一次查不到直接 failed
    report = drain_zhichi_outbox(
        db_session, client=fake, settings=_Settings(zhichi_writeback_max_attempts=1)
    )
    assert report.failed == 1
    assert report.sent == 0


def test_drain_supply_status_2(db_session: Session) -> None:
    _seed(db_session, kind="supply", payload={"supply_note": "请补充截图"})
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "2"  # supply → 等待回复，不关单
    assert fake.replies[0].reply_content == "请补充截图"


def test_drain_progress_note_status_2(db_session: Session) -> None:
    _seed(db_session, kind="progress_note", payload={"note": "已完成第1个，剩余2个"})
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "2"


def test_drain_release_note_status_3(db_session: Session) -> None:
    _seed(db_session, kind="release_note", payload={"note": "已上线"})
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "3"


def test_drain_status_released_uses_hub_reply(db_session: Session) -> None:
    _seed(db_session, kind="status", payload={"to_status": "released"})
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "3"
    assert fake.replies[0].reply_content == "hub级答复"


def test_drain_status_in_progress_skips(db_session: Session) -> None:
    _seed(db_session, kind="status", payload={"to_status": "in_progress"})
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.skipped == 1
    assert fake.replies == []


def test_drain_disabled_returns_empty(db_session: Session) -> None:
    _seed(db_session)
    db_session.commit()
    report = drain_zhichi_outbox(
        db_session, client=_FakeClient(), settings=_Settings(zhichi_writeback_enabled=False)
    )
    assert report.scanned == 0
