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


def test_drain_reply_closes_local_status(db_session: Session) -> None:
    """关单回写（status=3）真发成功后：ticket→closed、hub→resolved。"""
    t, hub, _ob = _seed(db_session)
    db_session.commit()
    report = drain_zhichi_outbox(db_session, client=_FakeClient(), settings=_Settings())
    assert report.sent == 1
    db_session.refresh(t)
    db_session.refresh(hub)
    assert t.status == "closed"
    assert hub.status == "resolved"


def test_drain_supply_does_not_close(db_session: Session) -> None:
    """补料（status=2）不关单 → 本地状态不动。"""
    t, hub, _ob = _seed(db_session, kind="supply", payload={"supply_note": "请补充截图"})
    db_session.commit()
    report = drain_zhichi_outbox(db_session, client=_FakeClient(), settings=_Settings())
    assert report.sent == 1
    db_session.refresh(t)
    db_session.refresh(hub)
    assert t.status == "received"  # 未关
    assert hub.status == "created"


def test_drain_reply_preserves_terminal_ticket(db_session: Session) -> None:
    """已终态（如投诉 closed）的 ticket 不被回写重置。"""
    t, hub, _ob = _seed(db_session)
    t.status = "closed"  # 已终态
    db_session.commit()
    report = drain_zhichi_outbox(db_session, client=_FakeClient(), settings=_Settings())
    assert report.sent == 1
    db_session.refresh(t)
    assert t.status == "closed"  # 保持，不重复流转


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


def _seed_native_flat(db: Session):  # type: ignore[no-untyped-def]
    """智齿原生扁平 payload（无 raw 外壳，字段在顶层）——线上真实格式。"""
    if db.query(Source).filter_by(code="zhichi").first() is None:
        db.add(Source(code="zhichi", name="智齿"))
    t = Ticket(
        short_code="TKT-ZF",
        source_code="zhichi",
        source_ticket_id="ZTF",
        type="Raw",
        status="received",
        # 顶层直接是 ticket_*，无 raw；title 是改写后的（标题优化）
        source_payload={
            "ticketid": "ZTF",
            "ticket_title": "客户留言-18279172007",
            "ticket_content": "<p>开票时如何关闭默认是否享受优惠</p>",
            "ticket_level": 0,
            "deal_agent_name": "",
        },
        title="开票时如何关闭默认是否享受优惠",  # 被标题优化改写过
        body="<p>开票时如何关闭默认是否享受优惠</p>",
    )
    db.add(t)
    db.flush()
    hub = HubIssue(short_code="HUB-ZF", type="Operation", title="x", status="created")
    db.add(hub)
    db.flush()
    ob = SyncOutbox(
        kind="reply",
        target_source_code="zhichi",
        ticket_id=t.id,
        source_ticket_id="ZTF",
        hub_issue_id=hub.id,
        payload={"reply_content": "操作路径如下…"},
        status="pending",
    )
    db.add(ob)
    db.flush()
    return t, hub, ob


def test_drain_native_flat_uses_original_zhichi_fields(db_session: Session) -> None:
    """原生扁平 payload：回写智齿的 ticket_title/content/level 必须取智齿侧原始值
    （顶层字段），不能回落到被改写过的 ticket.title/body——否则智齿报 400016 已过期。"""
    _seed_native_flat(db_session)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    req = fake.replies[0]
    # 关键：发回智齿的是原始 ticket_title，不是改写后的 ticket.title
    assert req.ticket_title == "客户留言-18279172007"
    assert req.ticket_content == "<p>开票时如何关闭默认是否享受优惠</p>"
    assert req.ticket_level == "0"
    # deal_agent_name 空 → 回落默认坐席
    assert req.reply_agent_name == "莉莉"
