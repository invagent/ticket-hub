"""EscalationIngester tests — payload parse, ticket+attachments, dedup, identity."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import Attachment, Source, Ticket
from app.services.ingest.escalation_ingester import (
    EscalationIngester,
    IngestError,
    parse_escalation_payload,
)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ai_cs", name="AI 客服"))
    db_session.commit()
    return db_session


def _payload(**ov) -> dict:  # type: ignore[type-arg, no-untyped-def]
    base = {
        "session_id": "sess-100",
        "original_question": "数电开票点击开具没反应",
        "ai_answer": "请确认已完成税局认证",
        "dissatisfaction": "认证做了，还是开不了",
        "product_line_code": "cloud-fapiao",
        "module": "数电开票",
        "customer": {"erp_uid": "ERP9", "mobile": "13800000000", "name": "张三"},
        "attachments": [{"url": "https://x/err.png", "filename": "err.png"}],
    }
    base.update(ov)
    return base


def test_parse_minimal_and_aliases() -> None:
    p = parse_escalation_payload({"sessionId": "s1", "question": "q", "answer": "a"})
    assert p.session_id == "s1" and p.original_question == "q" and p.ai_answer == "a"


def test_parse_missing_session_raises() -> None:
    with pytest.raises(IngestError, match="session_id"):
        parse_escalation_payload({"original_question": "q"})


def test_parse_missing_question_raises() -> None:
    with pytest.raises(IngestError, match="original_question"):
        parse_escalation_payload({"session_id": "s"})


def test_ingest_creates_ticket_triple_and_attachment(world: Session) -> None:
    res = EscalationIngester(world).ingest(_payload())
    world.commit()
    assert not res.deduped
    assert len(res.attachment_ids) == 1

    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.source_code == "ai_cs"
    assert t.source_ticket_id == "sess-100"
    assert t.body == "数电开票点击开具没反应"
    triple = t.source_payload["ai_cs"]
    assert triple["ai_answer"] == "请确认已完成税局认证"
    assert triple["dissatisfaction"] == "认证做了，还是开不了"

    att = world.get(Attachment, res.attachment_ids[0])
    assert att is not None
    assert att.source_url == "https://x/err.png"
    assert att.kind == "image" and att.vision_status == "pending"


def test_ingest_dedup_on_session(world: Session) -> None:
    EscalationIngester(world).ingest(_payload())
    world.commit()
    again = EscalationIngester(world).ingest(_payload())
    world.commit()
    assert again.deduped is True
    assert world.query(Ticket).filter_by(source_code="ai_cs").count() == 1


def test_ingest_without_attachments(world: Session) -> None:
    res = EscalationIngester(world).ingest(_payload(attachments=[]))
    world.commit()
    assert res.attachment_ids == []
    assert world.query(Attachment).count() == 0


def test_long_question_truncated_to_title(world: Session) -> None:
    res = EscalationIngester(world).ingest(_payload(original_question="问" * 200))
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert len(t.title) <= 120
    assert len(t.body) == 200  # body 保留全文


def test_parse_feedback_loop_fields_with_junk_filtered() -> None:
    p = parse_escalation_payload(
        _payload(
            conversation=[
                {"role": "user", "text": "q1"},
                "junk",
                {"role": "assistant", "text": "a1"},
            ],
            cited_knowledge=[{"type": "wiki", "title": "开票指引", "score": 0.91}, 42],
            skills_used=["customer-service", "", 7, "customer-service-feishu"],
        )
    )
    assert p.conversation == [{"role": "user", "text": "q1"}, {"role": "assistant", "text": "a1"}]
    assert p.cited_knowledge == [{"type": "wiki", "title": "开票指引", "score": 0.91}]
    assert p.skills_used == ["customer-service", "customer-service-feishu"]


def test_parse_feedback_loop_fields_default_empty() -> None:
    p = parse_escalation_payload({"session_id": "s1", "question": "q"})
    assert p.conversation == [] and p.cited_knowledge == [] and p.skills_used == []


def test_ingest_stores_feedback_loop_fields(world: Session) -> None:
    res = EscalationIngester(world).ingest(
        _payload(
            conversation=[{"role": "user", "text": "开不了票"}],
            cited_knowledge=[{"type": "faq", "id": "F1", "title": "认证步骤"}],
            skills_used=["customer-service"],
        )
    )
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    ai = t.source_payload["ai_cs"]
    assert ai["conversation"] == [{"role": "user", "text": "开不了票"}]
    assert ai["cited_knowledge"] == [{"type": "faq", "id": "F1", "title": "认证步骤"}]
    assert ai["skills_used"] == ["customer-service"]


def test_ingest_legacy_payload_omits_feedback_keys(world: Session) -> None:
    res = EscalationIngester(world).ingest(_payload())
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert set(t.source_payload["ai_cs"]) == {"original_question", "ai_answer", "dissatisfaction"}
