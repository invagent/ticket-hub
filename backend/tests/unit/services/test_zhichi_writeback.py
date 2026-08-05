"""智齿出站 writeback sender 单测。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from adapters.zhichi import ZhichiBusinessError
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


def _seed(  # type: ignore[no-untyped-def]
    db: Session,
    *,
    deal_agent_name: str = "莉莉",
    kind: str = "reply",
    payload=None,
    reply_is_draft: bool = False,
):
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
        reply_is_draft=reply_is_draft,
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
    t, _hub, _ob = _seed(db_session)
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


def test_drain_status_released_ignores_draft_reply(db_session: Session) -> None:
    """草稿态 hub 答复不能被当 released 关单话术回写智齿。"""
    _seed(db_session, kind="status", payload={"to_status": "released"}, reply_is_draft=True)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "3"
    assert fake.replies[0].reply_content != "hub级答复"


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


class _BusinessErrorClient(_FakeClient):
    """reply_ticket 抛指定 ret_code 的 ZhichiBusinessError（模拟智齿业务级失败）。"""

    def __init__(self, ret_code: str, ret_msg: str = "") -> None:
        super().__init__()
        self._ret_code = ret_code
        self._ret_msg = ret_msg

    def reply_ticket(self, req):  # type: ignore[no-untyped-def]
        raise ZhichiBusinessError(
            op="save_ticket_reply", ret_code=self._ret_code, ret_msg=self._ret_msg
        )


def test_drain_ticket_closed_marks_skipped_and_closes_local(db_session: Session) -> None:
    """智齿侧工单已关闭（400258）：outbox 标 skipped（不重试）+ 本地关单收尾。"""
    t, hub, ob = _seed(db_session)
    db_session.commit()
    fake = _BusinessErrorClient("400258", "工单已关闭")
    report = drain_zhichi_outbox(db_session, client=fake, settings=_Settings())
    # 不计入 failed（不占转人工名额），走 skipped
    assert report.skipped == 1
    assert report.failed == 0
    db_session.refresh(t)
    db_session.refresh(hub)
    db_session.refresh(ob)
    # 本地收尾到位
    assert t.status == "closed"
    assert hub.status == "resolved"
    # outbox skipped + 只 attempts+1（不耗尽重试）
    assert ob.status == "skipped"
    assert ob.attempts == 1
    assert "已关闭" in (ob.last_error or "")


def test_drain_ticket_closed_records_timeline_node(db_session: Session) -> None:
    """400258 收尾在 status_history 留「工单已关闭」时间线节点（前端时间线直读）。"""
    from app.repositories.status_history import StatusHistoryRepository

    t, _hub, _ob = _seed(db_session)
    db_session.commit()
    drain_zhichi_outbox(
        db_session, client=_BusinessErrorClient("400258", "工单已关闭"), settings=_Settings()
    )
    rows = StatusHistoryRepository(db_session).find_for_entity(entity_type="ticket", entity_id=t.id)
    closed = [r for r in rows if r.to_status == "closed"]
    assert closed, "should record a closed transition on the ticket timeline"
    assert "已关闭" in (closed[-1].reason or "")


def test_drain_ticket_closed_skipped_not_redrained(db_session: Session) -> None:
    """标 skipped 后不再被 drain 重扫（drain 只取 status='pending'）。"""
    _t, _hub, ob = _seed(db_session)
    db_session.commit()
    drain_zhichi_outbox(db_session, client=_BusinessErrorClient("400258"), settings=_Settings())
    db_session.refresh(ob)
    assert ob.status == "skipped"
    # 第二轮：无 pending，scanned=0
    report2 = drain_zhichi_outbox(db_session, client=_FakeClient(), settings=_Settings())
    assert report2.scanned == 0


def test_drain_other_business_error_still_fails(db_session: Session) -> None:
    """非「已关闭」的业务错误（如 400016 已过期）仍走 failure 重试路径，不误当终态。"""
    t, _hub, ob = _seed(db_session)
    db_session.commit()
    fake = _BusinessErrorClient("400016", "获取工单信息已过期")
    report = drain_zhichi_outbox(
        db_session, client=fake, settings=_Settings(zhichi_writeback_max_attempts=1)
    )
    assert report.failed == 1
    assert report.skipped == 0
    db_session.refresh(t)
    db_session.refresh(ob)
    # 未收尾：本地状态不动
    assert t.status == "received"
    assert ob.status == "failed"
