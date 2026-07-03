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
