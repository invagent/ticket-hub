"""FeishuAiIngester tests — 复用 ai_cs 载荷，走 feishu_ai 来源。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import Attachment, Source, Ticket
from app.services.ingest.feishu_ai_ingester import FeishuAiIngester


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="feishu_ai", name="飞书AI"))
    db_session.commit()
    return db_session


def _payload(**ov) -> dict:  # type: ignore[type-arg, no-untyped-def]
    base = {
        "session_id": "fa-100",
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


def test_ingest_creates_feishu_ai_ticket(world: Session) -> None:
    res = FeishuAiIngester(world).ingest(_payload())
    world.commit()
    assert not res.deduped
    assert len(res.attachment_ids) == 1

    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.source_code == "feishu_ai"
    assert t.source_ticket_id == "fa-100"
    assert t.type == "Raw" and t.status == "received"
    assert t.body == "数电开票点击开具没反应"


def test_triple_archived_in_source_payload(world: Session) -> None:
    res = FeishuAiIngester(world).ingest(_payload())
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    triple = t.source_payload["ai_cs"]
    assert triple["ai_answer"] == "请确认已完成税局认证"
    assert triple["dissatisfaction"] == "认证做了，还是开不了"


def test_attachment_row_created(world: Session) -> None:
    res = FeishuAiIngester(world).ingest(_payload())
    world.commit()
    att = world.get(Attachment, res.attachment_ids[0])
    assert att is not None
    assert att.source_url == "https://x/err.png"
    assert att.kind == "image" and att.vision_status == "pending"


def test_ingest_dedup_on_session(world: Session) -> None:
    FeishuAiIngester(world).ingest(_payload())
    world.commit()
    again = FeishuAiIngester(world).ingest(_payload())
    world.commit()
    assert again.deduped is True
    assert world.query(Ticket).filter_by(source_code="feishu_ai").count() == 1


def test_long_question_truncated_to_title(world: Session) -> None:
    res = FeishuAiIngester(world).ingest(_payload(original_question="问" * 200))
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert len(t.title) <= 120
    assert len(t.body) == 200  # body 保留全文
