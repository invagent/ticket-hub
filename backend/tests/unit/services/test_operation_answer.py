"""Operation 自动答复单测。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsError
from adapters.ai_cs.types import ReplayResult
from app.models import AgentDecision, HubIssue, Source, Ticket
from app.services.agents.operation_answer import auto_answer_operation


@dataclass
class _S:
    operation_auto_reply_enabled: bool = True
    operation_auto_reply_min_length: int = 10
    knowledge_feedback_enabled: bool = True
    ai_cs_app_id: str = "x"
    ai_cs_app_key: str = "y"
    ai_cs_base_url: str = "http://localhost:9090"
    ai_cs_managed_skills: str = "customer-service"


class _FakeClient:
    def __init__(self, answer: str = "", raise_err: bool = False) -> None:
        self._answer = answer
        self._raise = raise_err

    def replay(self, **kw: object) -> ReplayResult:
        if self._raise:
            raise AiCsError("boom")
        return ReplayResult(answer=self._answer, cited_knowledge=[], skills_used=[], trace_id="t1")

    def close(self) -> None:
        pass


def _seed_op_hub(db: Session, *, source: str = "ksm") -> tuple[HubIssue, Ticket]:
    if db.query(Source).filter_by(code=source).first() is None:
        db.add(Source(code=source, name=source.upper()))
    hub = HubIssue(
        short_code=f"HUB-OP-{source}",
        type="Operation",
        title="开票失败",
        canonical_body="开票时提示网络错误",
        status="created",
        product="发票云",
        module="开票",
    )
    db.add(hub)
    db.flush()
    t = Ticket(
        short_code=f"TKT-OP-{source}",
        source_code=source,
        source_ticket_id=f"{source}-1",
        type="Raw",
        status="received",
        hub_issue_id=hub.id,
        title="开票失败",
        body="开票时提示网络错误",
    )
    db.add(t)
    db.flush()
    return hub, t


def test_auto_answer_d_sends(db_session: Session) -> None:
    from app.services.agents.operation_answer import AnswerRoute

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票，若仍失败请提供截图。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 1
    assert hub.reply_authored_by == "agent:ai_cs"
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal["branch"] == "D"


def test_auto_answer_c_requests_supply(db_session: Session) -> None:
    from app.models import SyncOutbox
    from app.services.agents.operation_answer import AnswerRoute

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="需要更多信息才能定位")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="C", supply_note="请提供开票报错截图"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 0  # 没答复，走补料
    ob = (
        db_session.query(SyncOutbox)
        .filter_by(hub_issue_id=hub.id, kind="supply")
        .first()
    )
    assert ob is not None
    assert ob.payload.get("supply_note") == "请提供开票报错截图"


def test_auto_answer_transfer_leaves_to_human(db_session: Session) -> None:
    from app.services.agents.operation_answer import AnswerRoute

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="无法回答")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="transfer"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.reply_content_version == 0


def test_auto_answer_replay_error_leaves_to_human(db_session: Session) -> None:
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(raise_err=True)
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False


def test_auto_answer_disabled(db_session: Session) -> None:
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    ok = auto_answer_operation(db_session, hub.id, settings=_S(operation_auto_reply_enabled=False))
    assert ok is False


def test_auto_answer_ai_cs_source_skipped(db_session: Session) -> None:
    hub, _t = _seed_op_hub(db_session, source="ai_cs")
    db_session.commit()
    # 即使 enabled，ai_cs 来源也不自动答复（走 reflect）
    ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False


# ---- answer-router _route_answer 单测 ----

from types import SimpleNamespace  # noqa: E402

from app.services.agents.operation_answer import AnswerRoute, _route_answer  # noqa: E402


class _FakeRouter:
    def __init__(self, content: str, raise_err: bool = False) -> None:
        self._content = content
        self._raise = raise_err

    def complete(self, messages: object, **kw: object) -> object:
        if self._raise:
            from app.core.llm_router import LLMRouterError

            raise LLMRouterError("boom")
        return SimpleNamespace(content=self._content, cost_usd=0.0, model="fake")


def test_route_answer_d() -> None:
    r = _route_answer(
        "开票失败", "请在设置页重新绑定后重试。",
        router=_FakeRouter('{"branch":"D","supply_note":""}'),
    )
    assert r.branch == "D"


def test_route_answer_c_with_supply_note() -> None:
    r = _route_answer(
        "开票失败", "需要更多信息",
        router=_FakeRouter('{"branch":"C","supply_note":"请提供开票报错截图"}'),
    )
    assert r.branch == "C"
    assert r.supply_note == "请提供开票报错截图"


def test_route_answer_transfer() -> None:
    r = _route_answer(
        "x", "无法回答",
        router=_FakeRouter('{"branch":"transfer","supply_note":""}'),
    )
    assert r.branch == "transfer"


def test_route_answer_llm_error_falls_back_transfer() -> None:
    r = _route_answer("x", "y", router=_FakeRouter("", raise_err=True))
    assert r.branch == "transfer"


def test_route_answer_illegal_branch_falls_back_transfer() -> None:
    r = _route_answer("x", "y", router=_FakeRouter('{"branch":"A","supply_note":""}'))
    assert r.branch == "transfer"
