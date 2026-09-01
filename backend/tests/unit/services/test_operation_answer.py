"""Operation 自动答复单测。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsError
from adapters.ai_cs.types import ReplayResult
from app.models import AgentDecision, HubIssue, Source, Ticket
from app.services.agents.operation_answer import AnswerRoute, auto_answer_operation


@dataclass
class _S:
    operation_auto_reply_enabled: bool = True
    operation_auto_reply_min_length: int = 10
    operation_auto_reply_batch: int = 10
    knowledge_feedback_enabled: bool = True
    ai_cs_app_id: str = "x"
    ai_cs_app_key: str = "y"
    ai_cs_base_url: str = "http://localhost:9090"
    ai_cs_managed_skills: str = "customer-service"
    default_pool_user_id: int | None = None
    operation_answer_accuracy_mode: str = "off"
    operation_answer_accuracy_threshold: int = 90


class _FakeClient:
    def __init__(self, answer: str = "", raise_err: bool = False) -> None:
        self._answer = answer
        self._raise = raise_err
        self.replay_kwargs: dict[str, object] = {}

    def replay(self, **kw: object) -> ReplayResult:
        self.replay_kwargs = kw
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
        op_status="processing",
        op_handler="agent",
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
    assert hub.op_status == "answered"
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal["branch"] == "D"


def test_auto_answer_d_persists_cited_knowledge_and_skills_used(db_session: Session) -> None:
    """D 分支发送成功必存 cited_knowledge/skills_used（供处理人事后标记诊断时
    还原黄金三元组；此前 replay 打完分就丢了，从未持久化）。"""
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    fake_cited = [{"type": "faq", "title": "开票失败排查", "score": 0.88}]

    def _replay(**kw: object) -> ReplayResult:
        fake.replay_kwargs = kw
        return ReplayResult(
            answer=fake._answer, cited_knowledge=fake_cited, skills_used=[], trace_id="t2"
        )

    fake.replay = _replay  # type: ignore[method-assign]
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        auto_answer_operation(
            db_session, hub.id, settings=_S(ai_cs_managed_skills="customer-service")
        )
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None
    assert d.proposal["cited_knowledge"] == fake_cited
    assert d.proposal["skills_used"] == ["customer-service"]


def test_auto_answer_passes_managed_skill_to_replay(db_session: Session) -> None:
    """replay 必须带受管理 skill（AI 客服服务端要求 skill 在受管理列表内）。"""
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        auto_answer_operation(
            db_session, hub.id, settings=_S(ai_cs_managed_skills="customer-service,cs-feishu")
        )
    # 取受管理列表第一个作默认
    assert fake.replay_kwargs.get("skill") == "customer-service"


def test_auto_answer_c_stays_processing_with_draft(db_session: Session) -> None:
    """route 判 C（需补料）→ 不再直接置补料中。留处理中 + 把需补内容写进处理说明
    草稿（reply_content + reply_is_draft），handler 用人工名（非 agent，避免被 drain
    重扫无限重答）；不打回客户（无 supply outbox）。"""
    from app.models import SyncOutbox

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
    assert hub.op_status == "processing"  # 留处理中，不再是 supplementing
    assert hub.op_handler != "agent"  # 人工名，避免被 drain 重扫
    assert hub.reply_content == "请提供开票报错截图"  # 需补内容进草稿
    assert hub.reply_is_draft is True
    assert hub.reply_content_version == 0  # 草稿未发（未经 author_reply）
    # 不打回客户 → 无 supply outbox（补料要人工点「补充资料」才入队）
    ob = db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id, kind="supply").first()
    assert ob is None
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal["branch"] == "C"
    assert d.proposal["supply_note"] == "请提供开票报错截图"


def test_auto_answer_transfer_leaves_to_human(db_session: Session) -> None:

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
    assert hub.op_status == "processing"
    assert hub.op_handler != "agent"


def test_auto_answer_d_short_answer_downgrades_to_transfer(db_session: Session) -> None:
    """route 判 D 但答复过短（< min_length）→ 不发客户，降级留主管。"""
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="好的")  # 2 字，短于 min_length=10
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.reply_content_version == 0  # 没发出去
    assert hub.op_status == "processing"
    assert hub.op_handler != "agent"


def test_auto_answer_d_transfer_keyword_downgrades_to_transfer(db_session: Session) -> None:
    """route 判 D 但答复含转人工兜底话术 → 不发客户，降级留主管。"""
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="抱歉，这个问题无法处理，建议您转人工客服咨询。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.reply_content_version == 0
    assert hub.op_status == "processing"


def test_auto_answer_c_empty_supply_note_downgrades_to_transfer(db_session: Session) -> None:
    """route 判 C 但 supply_note 为空 → 不拿 answer 当补料话术发客户，降级留主管。"""
    from app.models import SyncOutbox

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="无法处理")  # 若误用作补料话术会发这句给客户
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="C", supply_note=""),  # 空 supply_note
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    # 不该入 supply outbox（没拿 answer 当补料话术）
    ob = db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id, kind="supply").first()
    assert ob is None
    assert hub.op_status == "processing"
    assert hub.op_handler != "agent"


def test_auto_answer_replay_error_leaves_to_human(db_session: Session) -> None:
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(raise_err=True)
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.op_status == "exception"
    assert hub.op_handler != "agent"


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


def test_auto_answer_gate_classify_parked_skipped(db_session: Session) -> None:
    """闸门①停摆态（hub.status='pending_review'）不自动答复——即使 op_status/
    op_handler 是 processing/agent。"""
    hub, _t = _seed_op_hub(db_session)
    hub.status = "pending_review"
    db_session.commit()
    ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False


# ---- 答复准确率三态闸门（off/observe/enforce）----


def test_d_observe_mode_scores_but_sends(db_session: Session) -> None:
    """observe：打分记审计但照常直发客户 + answered。"""
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "observe"
    fake = _FakeClient(answer="您好，请在发票管理页重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D", supply_note=""),
        ),
        patch(
            "app.services.agents.operation_answer.score_answer_accuracy",
            return_value=AccuracyScore(accuracy=40, reason="低分也直发"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "answered"  # observe 低分仍直发
    assert hub.reply_content_version >= 1  # 真的发了（走 author_reply）
    assert hub.reply_is_draft is False
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal.get("accuracy") == 40


def test_d_enforce_low_accuracy_saves_draft_reviewing(db_session: Session) -> None:
    """enforce + <阈值：存草稿(不发) + reviewing + 转主管 + 无 outbox。"""
    from app.models import SyncOutbox
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "enforce"
    s.operation_answer_accuracy_threshold = 90
    fake = _FakeClient(answer="可能是网络问题，建议稍后再试。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D", supply_note=""),
        ),
        patch(
            "app.services.agents.operation_answer.score_answer_accuracy",
            return_value=AccuracyScore(accuracy=60, reason="依据不足"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "reviewing"
    assert hub.op_handler != "agent"  # 转兜底主管
    assert hub.reply_content == "可能是网络问题，建议稍后再试。"
    assert hub.reply_is_draft is True  # 草稿标记
    assert hub.reply_content_version == 0  # 未经 author_reply（未级联）
    # 不发客户 → 无 outbox
    assert db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id).count() == 0
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal["branch"] == "D_review"
    assert d.proposal.get("accuracy") == 60


def test_d_enforce_high_accuracy_sends(db_session: Session) -> None:
    """enforce + ≥阈值：直发 + answered。"""
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "enforce"
    fake = _FakeClient(answer="您好，请在【发票管理】页重新发起开票并保存。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D", supply_note=""),
        ),
        patch(
            "app.services.agents.operation_answer.score_answer_accuracy",
            return_value=AccuracyScore(accuracy=95, reason="准确"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "answered"
    assert hub.reply_is_draft is False


def test_d_review_mode_always_saves_draft_reviewing(db_session: Session) -> None:
    """review：无论准确率高低都存草稿(不发) + reviewing + 转主管 + 无 outbox。"""
    from app.models import SyncOutbox
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "review"
    fake = _FakeClient(answer="您好，请在【发票管理】页重新发起开票并保存。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D", supply_note=""),
        ),
        patch(
            "app.services.agents.operation_answer.score_answer_accuracy",
            return_value=AccuracyScore(accuracy=99, reason="准确"),  # 高分也不直发
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "reviewing"  # 高分也转审核
    assert hub.op_handler != "agent"  # 转兜底主管
    assert hub.reply_content == "您好，请在【发票管理】页重新发起开票并保存。"
    assert hub.reply_is_draft is True  # 草稿标记，未发
    assert hub.reply_content_version == 0  # 未经 author_reply（未级联）
    # 不发客户 → 无 outbox
    assert db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id).count() == 0
    d = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .first()
    )
    assert d is not None and d.proposal["branch"] == "D_review"
    assert d.proposal.get("mode") == "review"


# ---- 补料回流完整闭环 seam ----


def test_supply_refill_full_loop(db_session: Session) -> None:
    """完整闭环：判需补料→处理中(草稿,handler≠agent,drain 不扫)→点补充资料→
    补料中→客户重推→处理中/agent(drain 扫到)→AI 重答成功。"""
    from app.services.cascade.supply_sync import request_supply
    from app.services.ingest.ksm_ingester import KSMIngester

    hub, ticket = _seed_op_hub(db_session, source="ksm")
    db_session.commit()

    # 1. AI 判需补料 → 留处理中 + 草稿，handler=人工名
    fake_c = _FakeClient(answer="需要更多信息才能定位")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake_c),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="C", supply_note="请提供开票报错截图"),
        ),
    ):
        assert auto_answer_operation(db_session, hub.id, settings=_S()) is True
    db_session.refresh(hub)
    assert hub.op_status == "processing" and hub.op_handler != "agent"
    assert hub.reply_content == "请提供开票报错截图" and hub.reply_is_draft is True

    # 2. drain 一轮：handler≠agent → 不被扫（不抢人工处理中的单）
    report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 0

    # 3. 人工点「补充资料」→ supplementing
    request_supply(db_session, hub.id, note="请提供开票报错截图", requested_by="user:张三")
    db_session.refresh(hub)
    assert hub.op_status == "supplementing"

    # 4. 客户补料，KSM 重推同 billId → 转回 processing/agent
    ing = KSMIngester(db_session, default_pool_user_id=None)
    ing.ingest({"billId": ticket.source_ticket_id, "content": "这是补充的报错截图说明"})
    db_session.commit()
    db_session.refresh(hub)
    assert hub.op_status == "processing" and hub.op_handler == "agent"

    # 5. drain 再一轮：被扫到 → AI 重答成功
    fake_d = _FakeClient(answer="您好，请在【发票管理】重新发起开票并保存后重试。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake_d),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        report2 = drain_operation_auto_reply(db_session, settings=_S())
    assert report2.scanned == 1 and report2.answered == 1
    db_session.refresh(hub)
    assert hub.op_status == "answered"
    assert hub.reply_content_version == 1


# ---- drain_operation_auto_reply（异步扫描 + 补偿重试）----

from app.services.agents.operation_answer import (  # noqa: E402
    DrainReport,
    drain_operation_auto_reply,
)


def test_drain_disabled_returns_empty(db_session: Session) -> None:
    _seed_op_hub(db_session)
    db_session.commit()
    report = drain_operation_auto_reply(db_session, settings=_S(operation_auto_reply_enabled=False))
    assert report == DrainReport()


def test_drain_answers_unprocessed_hub(db_session: Session) -> None:
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 1
    assert report.answered == 1
    db_session.refresh(hub)
    assert hub.reply_content_version == 1


def test_drain_skips_already_answered(db_session: Session) -> None:
    """op_status=answered 的 hub 不再扫描（不再靠 reply_content_version 判断）。"""
    hub, _t = _seed_op_hub(db_session)
    hub.op_status = "answered"
    db_session.commit()
    report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 0


def test_drain_skips_transfer_recorded(db_session: Session) -> None:
    """已转人工（op_handler != agent）→ 不重扫（避免抢主管介入中的工单）。"""
    hub, _t = _seed_op_hub(db_session)
    hub.op_handler = "主管"
    db_session.commit()
    report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 0


def test_drain_scans_only_unprocessed_processing_agent(db_session: Session) -> None:
    """drain 只捞 op_status=processing 且 op_handler=agent（刚毕业未处理）；
    supplementing（主管收集中）不被捞。"""
    _hub_agent, _ = _seed_op_hub(db_session, source="ksm")
    hub_human, _ = _seed_op_hub(db_session, source="zhichi")
    hub_human.op_handler = "主管"
    hub_supplementing, _ = _seed_op_hub(db_session, source="zammad")
    hub_supplementing.op_status = "supplementing"
    hub_supplementing.op_handler = "主管"
    db_session.commit()

    fake = _FakeClient(raise_err=True)
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        report = drain_operation_auto_reply(db_session, settings=_S())

    assert report.scanned == 1  # 只有 _hub_agent


def test_drain_excludes_gate_classify_parked_pending_review(db_session: Session) -> None:
    """闸门①开时 Operation hub 停 hub.status='pending_review' 待主管确认分类
    （op_status 仍是毕业时预置的 processing/agent，未过闸门）。drain 只看
    op_status/op_handler，不看 hub.status，会误捞——必须加 hub.status 守卫。"""
    hub, _ = _seed_op_hub(db_session, source="ksm")
    hub.status = "pending_review"  # 闸门①停摆态：还没被主管确认分类
    db_session.commit()

    fake = _FakeClient(raise_err=True)
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        report = drain_operation_auto_reply(db_session, settings=_S())

    assert report.scanned == 0


def test_drain_includes_confirmed_created_hub(db_session: Session) -> None:
    """闸门①关（或已确认）的 Operation hub：hub.status='created' +
    op_status=processing/agent → drain 正常捞到。"""
    hub, _ = _seed_op_hub(db_session, source="ksm")
    assert hub.status == "created"
    db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 1
    assert report.answered == 1


def test_drain_excludes_ai_cs_source(db_session: Session) -> None:
    """ai_cs 来源提前排除，不进扫描集（免每轮空扫）。"""
    _seed_op_hub(db_session, source="ai_cs")
    db_session.commit()
    report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 0


def test_drain_replay_failure_falls_to_exception_not_rescanned(db_session: Session) -> None:
    """replay 系统故障 → op_status=exception + 转人工，不再无限重扫（人工介入）。"""
    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(raise_err=True)  # replay 抛 AiCsError → 落 exception 留主管
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        report1 = drain_operation_auto_reply(db_session, settings=_S())
    assert report1.scanned == 1
    assert report1.answered == 0
    db_session.refresh(hub)
    assert hub.op_status == "exception"
    assert hub.op_handler != "agent"
    # 已转人工（op_handler != agent）→ 下轮不再重扫
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        report2 = drain_operation_auto_reply(db_session, settings=_S())
    assert report2.scanned == 0


# ---- answer-router _route_answer 单测 ----

from types import SimpleNamespace  # noqa: E402

from app.services.agents.operation_answer import _route_answer  # noqa: E402


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
        "开票失败",
        "请在设置页重新绑定后重试。",
        router=_FakeRouter('{"branch":"D","supply_note":""}'),
    )
    assert r.branch == "D"


def test_route_answer_c_with_supply_note() -> None:
    r = _route_answer(
        "开票失败",
        "需要更多信息",
        router=_FakeRouter('{"branch":"C","supply_note":"请提供开票报错截图"}'),
    )
    assert r.branch == "C"
    assert r.supply_note == "请提供开票报错截图"


def test_route_answer_transfer() -> None:
    r = _route_answer(
        "x",
        "无法回答",
        router=_FakeRouter('{"branch":"transfer","supply_note":""}'),
    )
    assert r.branch == "transfer"


def test_route_answer_llm_error_falls_back_transfer() -> None:
    r = _route_answer("x", "y", router=_FakeRouter("", raise_err=True))
    assert r.branch == "transfer"


def test_route_answer_illegal_branch_falls_back_transfer() -> None:
    r = _route_answer("x", "y", router=_FakeRouter('{"branch":"A","supply_note":""}'))
    assert r.branch == "transfer"


# ---- replay 即时重试（网络/超时抖动兜底）----

from adapters.ai_cs import AiCsBusinessError, AiCsNetworkError  # noqa: E402
from app.services.agents.operation_answer import (  # noqa: E402
    _REPLAY_MAX_ATTEMPTS,
    _replay_with_retry,
)


class _SeqClient:
    """按预设序列抛异常/返回，记录调用次数。"""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def replay(self, **kw: object) -> ReplayResult:
        out = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return ReplayResult(answer=out, cited_knowledge=[], skills_used=[], trace_id="t")


def test_replay_retry_succeeds_after_timeout() -> None:
    """前两次超时，第三次成功 → 返回答复，共调用 3 次。"""
    c = _SeqClient([AiCsNetworkError("timed out"), AiCsNetworkError("timed out"), "答复内容"])
    result = _replay_with_retry(c, question="q", skill="customer-service", hub_id=1)
    assert result.answer == "答复内容"
    assert c.calls == 3


def test_replay_with_retry_returns_cited_knowledge(db_session: Session) -> None:
    """_replay_with_retry 返回完整 ReplayResult（带 cited_knowledge）供打分器用。"""
    fake = _FakeClient(answer="答复内容")
    result = _replay_with_retry(fake, question="q", skill=None, hub_id=1)
    assert result.answer == "答复内容"
    assert hasattr(result, "cited_knowledge")


def test_replay_retry_exhausts_and_raises() -> None:
    """连续超时耗尽重试 → 抛 AiCsNetworkError，调用次数=上限。"""
    c = _SeqClient([AiCsNetworkError("timed out")] * _REPLAY_MAX_ATTEMPTS)
    try:
        _replay_with_retry(c, question="q", skill="s", hub_id=1)
        raise AssertionError("should have raised")
    except AiCsNetworkError:
        pass
    assert c.calls == _REPLAY_MAX_ATTEMPTS


def test_replay_business_error_no_retry() -> None:
    """业务错误（skill 非法等）不重试，第一次即抛。"""
    c = _SeqClient([AiCsBusinessError("skill 非法"), "不该到这"])
    try:
        _replay_with_retry(c, question="q", skill="s", hub_id=1)
        raise AssertionError("should have raised")
    except AiCsBusinessError:
        pass
    assert c.calls == 1
