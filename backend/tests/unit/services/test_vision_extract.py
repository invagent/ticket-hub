"""vision_extract agent tests — gating, body append, skip/fail handling."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router.vision import VisionError, VisionResult
from app.models import Attachment, Source, Ticket
from app.services.agents.vision_extract import extract_ticket_attachments


class _FakeVisionClient:
    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises
        self.calls = 0

    def extract(self, *, prompt, image_url=None, image_bytes=None, mime="image/png"):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self._raises:
            raise VisionError("boom")
        return VisionResult(
            ocr_text="提示：商品不支持货物运输模式",
            ui_context="发票云-开票申请页",
            summary="开票报错",
            model="qwen-vl-max",
            cost_usd=0.001,
            raw={},
        )


@pytest.fixture(autouse=True)
def _vision_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _ticket(db: Session, **ov) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-VIS-1",
        "source_code": "ksm",
        "source_ticket_id": "vis-1",
        "type": "Raw",
        "status": "received",
        "title": "开票失败",
        "body": "客户描述：开票点不了",
    }
    base.update(ov)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _att(db: Session, ticket: Ticket, **ov) -> Attachment:  # type: ignore[no-untyped-def]
    base = {
        "ticket_id": ticket.id,
        "source_url": "https://x/err.png",
        "kind": "image",
        "vision_status": "pending",
    }
    base.update(ov)
    a = Attachment(**base)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_extracts_and_appends_to_body(world: Session) -> None:
    t = _ticket(world)
    a = _att(world, t)
    rep = extract_ticket_attachments(t.id, db=world, client=_FakeVisionClient())  # type: ignore[arg-type]
    assert rep is not None and rep.extracted == 1 and rep.appended_to_body
    world.refresh(t)
    world.refresh(a)
    assert a.vision_status == "extracted"
    assert a.vision_model == "qwen-vl-max"
    assert "[附件识别]" in (t.body or "")
    assert "商品不支持货物运输模式" in (t.body or "")
    assert "客户描述" in (t.body or "")  # 原 body 保留


def test_disabled_returns_none(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_ENABLED", "false")
    get_settings.cache_clear()
    t = _ticket(world)
    _att(world, t)
    assert extract_ticket_attachments(t.id, db=world, client=_FakeVisionClient()) is None  # type: ignore[arg-type]


def test_non_image_not_processed(world: Session) -> None:
    t = _ticket(world)
    _att(world, t, kind="pdf")
    fake = _FakeVisionClient()
    rep = extract_ticket_attachments(t.id, db=world, client=fake)  # type: ignore[arg-type]
    assert rep is not None and rep.extracted == 0
    assert fake.calls == 0


def test_no_source_url_skipped(world: Session) -> None:
    t = _ticket(world)
    a = _att(world, t, source_url=None, storage_key="minio/key")
    rep = extract_ticket_attachments(t.id, db=world, client=_FakeVisionClient())  # type: ignore[arg-type]
    assert rep is not None and rep.skipped == 1
    world.refresh(a)
    assert a.vision_status == "skipped"


def test_vision_failure_marked_and_swallowed(world: Session) -> None:
    t = _ticket(world)
    a = _att(world, t)
    rep = extract_ticket_attachments(t.id, db=world, client=_FakeVisionClient(raises=True))  # type: ignore[arg-type]
    assert rep is not None and rep.failed == 1 and not rep.appended_to_body
    world.refresh(a)
    assert a.vision_status == "failed"


def test_respects_max_images(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TICKET", "2")
    get_settings.cache_clear()
    t = _ticket(world)
    for i in range(4):
        _att(world, t, source_url=f"https://x/{i}.png")
    fake = _FakeVisionClient()
    rep = extract_ticket_attachments(t.id, db=world, client=fake)  # type: ignore[arg-type]
    assert rep is not None and rep.extracted == 2  # capped
    assert fake.calls == 2


def test_already_extracted_not_reprocessed(world: Session) -> None:
    t = _ticket(world)
    _att(world, t, vision_status="extracted")
    fake = _FakeVisionClient()
    rep = extract_ticket_attachments(t.id, db=world, client=fake)  # type: ignore[arg-type]
    assert rep is not None and rep.extracted == 0
    assert fake.calls == 0
