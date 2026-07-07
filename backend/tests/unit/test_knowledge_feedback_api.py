"""Phase 1 知识反哺 supervisor API tests.

Auth gate + feature gate (503 when off) + happy paths through a fake
AiCsClient (build_client patched, so no network), + publish audit + escalation
context.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from adapters.ai_cs import (
    AiCsBusinessError,
    DraftSummary,
    ReplayResult,
    SkillDetail,
    SkillFile,
    SkillSummary,
    SkillVersion,
)
from app.api.auth import issue_jwt
from app.models import Source, StatusHistory, Ticket, User


def _bearer(user_id: int, *, role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="carol", role=role)
    return {"Authorization": f"Bearer {token}"}


class FakeAiCsClient:
    """Records calls; returns canned adapter DTOs. Optionally raises."""

    def __init__(self, *, raise_business: bool = False) -> None:
        self.raise_business = raise_business
        self.calls: list[tuple] = []
        self.closed = False

    def _guard(self) -> None:
        if self.raise_business:
            raise AiCsBusinessError("版本不存在", error_code="400000")

    def list_skills(self) -> list[SkillSummary]:
        self._guard()
        return [
            SkillSummary(
                skill_name="customer-service",
                published_version="customer-service:V3",
                operator="admin",
                updated_at="2026-06-29T10:00:00+00:00",
                files=[SkillFile(filename="SKILL.md", filepath="SKILL.md")],
            )
        ]

    def get_skill(self, name: str) -> SkillDetail:
        self._guard()
        return SkillDetail(
            skill_name=name,
            published_version=f"{name}:V3",
            published_operator="admin",
            published_reason="优化",
            published_files=[SkillFile(filename="SKILL.md", filepath="SKILL.md", content="body")],
            history=[
                SkillVersion(
                    version=f"{name}:V3",
                    status="published",
                    operator="admin",
                    reason="优化",
                    created_at="2026-06-29T10:00:00+00:00",
                )
            ],
        )

    def list_drafts(self, name: str) -> list[DraftSummary]:
        self._guard()
        return []

    def create_draft(self, name: str, *, files, operator: str, reason: str) -> str:
        self.calls.append(("create_draft", name, files, operator, reason))
        self._guard()
        return f"{name}:V4"

    def publish_draft(self, name: str, version: str) -> None:
        self.calls.append(("publish", name, version))
        self._guard()

    def replay(
        self,
        *,
        session_id=None,
        question=None,
        skill=None,
        use_latest_knowledge=True,
        skill_draft_version=None,
    ) -> ReplayResult:
        self.calls.append(("replay", session_id, question, skill, skill_draft_version))
        self._guard()
        return ReplayResult(
            answer="支持，允许范围由税局规则决定",
            cited_knowledge=[{"url": "https://yuque.com/x"}],
            skills_used=["customer-service"],
            trace_id="tid-1",
        )

    def close(self) -> None:
        self.closed = True


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: FakeAiCsClient) -> None:
    monkeypatch.setattr("app.services.knowledge_feedback.build_client", lambda _s: fake)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.commit()
    return db_session


# ---- auth + feature gate ---------------------------------------------


def test_requires_supervisor(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/supervisor/ai-cs/skills", headers=_bearer(3, role="member"))
    assert r.status_code == 403


def test_status_default_disabled(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/supervisor/ai-cs/status", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["configured"] is False
    assert "customer-service" in body["managed_skills"]


def test_skills_disabled_returns_503(app_client: TestClient, world: Session) -> None:
    # No patch → real build_client → feature off → 503
    r = app_client.get("/api/supervisor/ai-cs/skills", headers=_bearer(2))
    assert r.status_code == 503


# ---- happy paths (fake client) --------------------------------------


def test_list_skills_ok(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAiCsClient()
    _patch_client(monkeypatch, fake)
    r = app_client.get("/api/supervisor/ai-cs/skills", headers=_bearer(2))
    assert r.status_code == 200
    assert r.json()[0]["published_version"] == "customer-service:V3"
    assert fake.closed is True  # client always closed


def test_get_skill_ok(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, FakeAiCsClient())
    r = app_client.get("/api/supervisor/ai-cs/skills/customer-service", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["published_files"][0]["content"] == "body"
    assert body["history"][0]["status"] == "published"


def test_create_draft_passes_operator_and_reason(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAiCsClient()
    _patch_client(monkeypatch, fake)
    r = app_client.post(
        "/api/supervisor/ai-cs/skills/customer-service/drafts",
        headers=_bearer(2),
        json={
            "files": [{"filename": "SKILL.md", "filepath": "SKILL.md", "content": "x"}],
            "reason": "改产品识别规则",
        },
    )
    assert r.status_code == 200
    assert r.json()["version"] == "customer-service:V4"
    call = next(c for c in fake.calls if c[0] == "create_draft")
    assert call[3] == "user:carol" and call[4] == "改产品识别规则"


def test_replay_ok(app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAiCsClient()
    _patch_client(monkeypatch, fake)
    r = app_client.post(
        "/api/supervisor/ai-cs/replay",
        headers=_bearer(2),
        json={
            "question": "星瀚支持电子行程单吗",
            "skill": "customer-service",
            "skill_draft_version": "customer-service:V4",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("支持")
    assert body["cited_knowledge"][0]["url"].startswith("https://")
    call = next(c for c in fake.calls if c[0] == "replay")
    assert call[4] == "customer-service:V4"


def test_replay_requires_input(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, FakeAiCsClient())
    r = app_client.post("/api/supervisor/ai-cs/replay", headers=_bearer(2), json={})
    assert r.status_code == 422


def test_business_error_maps_400(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, FakeAiCsClient(raise_business=True))
    r = app_client.get("/api/supervisor/ai-cs/skills", headers=_bearer(2))
    assert r.status_code == 400
    assert "版本不存在" in r.json()["detail"]


# ---- publish + audit -------------------------------------------------


def test_publish_writes_ticket_audit(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.add(Source(code="ai_cs", name="AI 客服"))
    world.add(
        Ticket(
            id=500,
            short_code="TKT-000500",
            source_code="ai_cs",
            source_ticket_id="sess-1",
            type="Raw",
            status="received",
            title="投诉",
            source_payload={"ai_cs": {"original_question": "q", "ai_answer": "a"}},
        )
    )
    world.commit()
    fake = FakeAiCsClient()
    _patch_client(monkeypatch, fake)
    r = app_client.post(
        "/api/supervisor/ai-cs/publish",
        headers=_bearer(2),
        json={"skill_name": "customer-service", "version": "customer-service:V4", "ticket_id": 500},
    )
    assert r.status_code == 200
    assert r.json()["published"] is True
    assert ("publish", "customer-service", "customer-service:V4") in fake.calls

    audit = (
        world.query(StatusHistory)
        .filter_by(entity_type="ticket", entity_id=500)
        .order_by(StatusHistory.id.desc())
        .first()
    )
    assert audit is not None
    assert audit.metadata_["kind"] == "knowledge_revision"
    assert audit.metadata_["version"] == "customer-service:V4"


def test_publish_without_ticket_no_audit(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, FakeAiCsClient())
    r = app_client.post(
        "/api/supervisor/ai-cs/publish",
        headers=_bearer(2),
        json={"skill_name": "customer-service", "version": "customer-service:V4"},
    )
    assert r.status_code == 200


# ---- escalation context ----------------------------------------------


def test_escalation_context_for_ai_cs_ticket(app_client: TestClient, world: Session) -> None:
    world.add(Source(code="ai_cs", name="AI 客服"))
    world.add(
        Ticket(
            id=600,
            short_code="TKT-000600",
            source_code="ai_cs",
            source_ticket_id="sess-9",
            type="Raw",
            status="received",
            title="投诉",
            body="航空电子行程单支持吗",
            source_payload={
                "ai_cs": {
                    "original_question": "航空电子行程单支持吗",
                    "ai_answer": "不支持",
                    "dissatisfaction": "明明支持",
                    "conversation": [{"role": "user", "text": "航空电子行程单支持吗"}],
                    "cited_knowledge": [{"type": "wiki", "title": "行程单说明"}],
                    "skills_used": ["customer-service"],
                }
            },
        )
    )
    world.commit()
    r = app_client.get("/api/supervisor/tickets/600/escalation-context", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["is_escalation"] is True
    assert body["session_id"] == "sess-9"
    assert body["ai_answer"] == "不支持"
    assert body["dissatisfaction"] == "明明支持"
    assert body["conversation"] == [{"role": "user", "text": "航空电子行程单支持吗"}]
    assert body["cited_knowledge"] == [{"type": "wiki", "title": "行程单说明"}]
    assert body["skills_used"] == ["customer-service"]


def test_escalation_context_legacy_payload_defaults_empty(
    app_client: TestClient, world: Session
) -> None:
    world.add(Source(code="ai_cs", name="AI 客服"))
    world.add(
        Ticket(
            id=602,
            short_code="TKT-000602",
            source_code="ai_cs",
            source_ticket_id="sess-old",
            type="Raw",
            status="received",
            title="旧载荷",
            source_payload={"ai_cs": {"original_question": "q", "ai_answer": "a"}},
        )
    )
    world.commit()
    r = app_client.get("/api/supervisor/tickets/602/escalation-context", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["conversation"] == []
    assert body["cited_knowledge"] == []
    assert body["skills_used"] == []


def test_escalation_context_non_ai_cs(app_client: TestClient, world: Session) -> None:
    world.add(Source(code="ksm", name="KSM"))
    world.add(
        Ticket(
            id=601,
            short_code="TKT-000601",
            source_code="ksm",
            source_ticket_id="BILL-1",
            type="Raw",
            status="received",
            title="工单",
        )
    )
    world.commit()
    r = app_client.get("/api/supervisor/tickets/601/escalation-context", headers=_bearer(2))
    assert r.status_code == 200
    assert r.json()["is_escalation"] is False


# ---- 反思诊断工作台：diagnosis + reflect --------------------------------


def _mk_escalation(
    world: Session, ticket_id: int = 700, *, predicted_type: str | None = None
) -> None:
    if not world.query(Source).filter_by(code="ai_cs").count():
        world.add(Source(code="ai_cs", name="AI 客服"))
    world.add(
        Ticket(
            id=ticket_id,
            short_code=f"TKT-{ticket_id:06d}",
            source_code="ai_cs",
            source_ticket_id=f"sess-{ticket_id}",
            type="Raw",
            status="received",
            title="开票超时",
            predicted_type=predicted_type,
            source_payload={
                "ai_cs": {
                    "original_question": "开票超时",
                    "ai_answer": "请实名认证",
                    "dissatisfaction": "做了还是超时",
                    "cited_knowledge": [{"type": "faq", "title": "超时排查", "score": 0.71}],
                }
            },
        )
    )
    world.commit()


def test_save_diagnosis_and_context_roundtrip(app_client: TestClient, world: Session) -> None:
    _mk_escalation(world)
    r = app_client.put(
        "/api/supervisor/tickets/700/diagnosis",
        headers=_bearer(2),
        json={"cause": "skill", "correct_answer": "实为通道拥堵"},
    )
    assert r.status_code == 200
    d = r.json()["diagnosis"]
    assert d["cause"] == "skill" and d["correct_answer"] == "实为通道拥堵"
    assert d["by"] == "user:2" and d["at"]

    ctx = app_client.get(
        "/api/supervisor/tickets/700/escalation-context", headers=_bearer(2)
    ).json()
    assert ctx["diagnosis"]["cause"] == "skill"

    # 审计行落 status_history（状态不变）
    rows = world.query(StatusHistory).filter_by(entity_type="ticket", entity_id=700).all()
    assert any((h.metadata_ or {}).get("kind") == "escalation_diagnosis" for h in rows)


def test_clear_diagnosis(app_client: TestClient, world: Session) -> None:
    _mk_escalation(world, 701)
    app_client.put(
        "/api/supervisor/tickets/701/diagnosis",
        headers=_bearer(2),
        json={"cause": "retrieval"},
    )
    r = app_client.put(
        "/api/supervisor/tickets/701/diagnosis", headers=_bearer(2), json={"cause": None}
    )
    assert r.status_code == 200 and r.json()["diagnosis"] is None
    ctx = app_client.get(
        "/api/supervisor/tickets/701/escalation-context", headers=_bearer(2)
    ).json()
    assert ctx["diagnosis"] is None


def test_multi_cause_checklist_roundtrip(app_client: TestClient, world: Session) -> None:
    """ADR-0016 P3 决策 6：多病因集合 + 每病因修复清单，勾选保留、全绿 resolved。"""
    _mk_escalation(world, 705)
    # 判定双病因 → 清单两项全未勾
    r = app_client.put(
        "/api/supervisor/tickets/705/diagnosis",
        headers=_bearer(2),
        json={"causes": ["knowledge", "skill"], "correct_answer": "实为通道拥堵"},
    )
    assert r.status_code == 200
    d = r.json()["diagnosis"]
    assert d["causes"] == ["knowledge", "skill"]
    assert d["cause"] == "knowledge"  # 主病因 = 第一个（旧消费方/队列过滤兼容）
    assert d["checklist"] == [
        {"cause": "knowledge", "done": False},
        {"cause": "skill", "done": False},
    ]
    assert d["resolved"] is False

    # 勾掉 skill 一项（重发集合 + checklist_done）——knowledge 的未勾状态保留
    r = app_client.put(
        "/api/supervisor/tickets/705/diagnosis",
        headers=_bearer(2),
        json={"causes": ["knowledge", "skill"], "checklist_done": {"skill": True}},
    )
    d = r.json()["diagnosis"]
    assert d["checklist"] == [
        {"cause": "knowledge", "done": False},
        {"cause": "skill", "done": True},
    ]
    assert d["resolved"] is False

    # 全部勾完 → resolved 闭环；且不带 checklist_done 重存时 done 进度保留
    r = app_client.put(
        "/api/supervisor/tickets/705/diagnosis",
        headers=_bearer(2),
        json={"causes": ["knowledge", "skill"], "checklist_done": {"knowledge": True}},
    )
    d = r.json()["diagnosis"]
    assert d["resolved"] is True
    r = app_client.put(
        "/api/supervisor/tickets/705/diagnosis",
        headers=_bearer(2),
        json={"causes": ["knowledge", "skill"]},
    )
    assert r.json()["diagnosis"]["resolved"] is True  # 进度未被冲掉


def test_diagnosis_invalid_cause_422(app_client: TestClient, world: Session) -> None:
    _mk_escalation(world, 702)
    r = app_client.put(
        "/api/supervisor/tickets/702/diagnosis", headers=_bearer(2), json={"cause": "both"}
    )
    assert r.status_code == 422


def test_diagnosis_non_escalation_404(app_client: TestClient, world: Session) -> None:
    world.add(Source(code="ksm", name="KSM"))
    world.add(
        Ticket(
            id=703,
            short_code="TKT-000703",
            source_code="ksm",
            source_ticket_id="B-1",
            type="Raw",
            status="received",
            title="非 escalation",
        )
    )
    world.commit()
    r = app_client.put(
        "/api/supervisor/tickets/703/diagnosis", headers=_bearer(2), json={"cause": "skill"}
    )
    assert r.status_code == 404


def test_reflect_runs_and_caches(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_escalation(world, 704)
    # 先存人工正解，端点应把它喂给 agent
    app_client.put(
        "/api/supervisor/tickets/704/diagnosis",
        headers=_bearer(2),
        json={"correct_answer": "实为通道拥堵"},
    )

    from app.services.knowledge_feedback import reflect as rf

    seen: dict = {}

    def fake_run_reflect(**kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return rf.ReflectResult(
            steps=[{"title": "t", "detail": "d", "verdict": None, "good": None}],
            causes=["skill"],
            confidence=0.9,
            reason="r",
            suggested_revision="改规则 2",
            cost_usd=0.001,
            model="glm-4-flash",
        )

    monkeypatch.setattr(rf, "run_reflect", fake_run_reflect)
    r = app_client.post("/api/supervisor/tickets/704/reflect", headers=_bearer(2))
    assert r.status_code == 200
    refl = r.json()["reflection"]
    assert refl["cause"] == "skill" and refl["suggested_revision"] == "改规则 2"
    assert seen["correct_answer"] == "实为通道拥堵"
    assert seen["cited_knowledge"][0]["title"] == "超时排查"

    # 缓存进 escalation-context
    ctx = app_client.get(
        "/api/supervisor/tickets/704/escalation-context", headers=_bearer(2)
    ).json()
    assert ctx["reflection"]["cause"] == "skill"


def test_reflect_llm_unavailable_503(
    app_client: TestClient, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_escalation(world, 705)
    from app.core.llm_router import LLMRouterError
    from app.services.knowledge_feedback import reflect as rf

    def boom(**kw):  # type: ignore[no-untyped-def]
        raise LLMRouterError("no providers configured", attempts=[])

    monkeypatch.setattr(rf, "run_reflect", boom)
    r = app_client.post("/api/supervisor/tickets/705/reflect", headers=_bearer(2))
    assert r.status_code == 503


def test_reflect_non_escalation_404(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/api/supervisor/tickets/99999/reflect", headers=_bearer(2))
    assert r.status_code == 404


def test_escalation_pending_diagnosis_queue(app_client: TestClient, world: Session) -> None:
    _mk_escalation(world, 710)  # 未诊断 → 在队列
    _mk_escalation(world, 711)
    # 711 已判定病因 → 不在队列
    app_client.put(
        "/api/supervisor/tickets/711/diagnosis", headers=_bearer(2), json={"cause": "skill"}
    )
    # 只填了正解没定病因 → 仍在队列
    _mk_escalation(world, 712)
    app_client.put(
        "/api/supervisor/tickets/712/diagnosis",
        headers=_bearer(2),
        json={"correct_answer": "正解"},
    )
    r = app_client.get("/api/supervisor/escalation-pending-diagnosis", headers=_bearer(2))
    assert r.status_code == 200
    ids = {it["ticket_id"] for it in r.json()["items"]}
    assert 710 in ids and 712 in ids
    assert 711 not in ids
    row = next(it for it in r.json()["items"] if it["ticket_id"] == 710)
    assert row["dissatisfaction"] == "做了还是超时"


def test_escalation_queue_operation_only_filter(app_client: TestClient, world: Session) -> None:
    """ADR-0016：反思队列只收 Operation（含未分类 NULL），Bug/Demand 走 Linear 不进队列。"""
    _mk_escalation(world, 720, predicted_type="Operation")
    _mk_escalation(world, 721, predicted_type="Bug_fix")
    _mk_escalation(world, 722, predicted_type="Demand")
    _mk_escalation(world, 723, predicted_type=None)  # 分类失败/未跑 → 保留人工可见
    r = app_client.get("/api/supervisor/escalation-pending-diagnosis", headers=_bearer(2))
    ids = {it["ticket_id"] for it in r.json()["items"]}
    assert 720 in ids and 723 in ids
    assert 721 not in ids and 722 not in ids
