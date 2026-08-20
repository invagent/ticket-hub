"""KSMIngester attachment row creation (queued, no download)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attachment, ProductLine, Source, User
from app.services.ingest.ksm_ingester import KSMIngester


@pytest.fixture
def ingest_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.commit()
    return db_session


def test_ingest_creates_attachment_rows(ingest_world: Session) -> None:
    payload = {
        "billId": "BILL-ATT-1",
        "title": "报错",
        "content": "见附件",
        "attachment_urls": ["http://ksm/a.png", "http://ksm/b.png"],
        "_subscribe_callback": {},
    }
    res = KSMIngester(ingest_world).ingest(payload)
    ingest_world.commit()

    rows = (
        ingest_world.execute(select(Attachment).where(Attachment.ticket_id == res.ticket_id))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.source_url for r in rows} == {"http://ksm/a.png", "http://ksm/b.png"}
    assert all(r.vision_status == "queued" for r in rows)
    assert all(r.storage_key is None for r in rows)
    assert all(r.kind == "image" for r in rows)


def test_ingest_uses_ksm_name_as_filename(ingest_world: Session) -> None:
    """新格式 attachments=[{url,name}]：filename 用真实 name，kind 按 name 扩展名判。
    url 是 accessory!download.action 动作端点，不能当文件名。"""
    payload = {
        "billId": "BILL-ATT-NAME",
        "title": "报错",
        "content": "见附件",
        "attachments": [
            {"url": "https://ksm/system/accessory!download.action?id=1", "name": "发票问题.docx"},
            {"url": "https://ksm/system/accessory!download.action?id=2", "name": "截图.png"},
        ],
        "_subscribe_callback": {},
    }
    res = KSMIngester(ingest_world).ingest(payload)
    ingest_world.commit()

    rows = {
        r.filename: r
        for r in ingest_world.execute(
            select(Attachment).where(Attachment.ticket_id == res.ticket_id)
        )
        .scalars()
        .all()
    }
    assert set(rows) == {"发票问题.docx", "截图.png"}
    # docx → other（不进 OCR）；png → image（进 OCR）
    assert rows["发票问题.docx"].kind == "other"
    assert rows["截图.png"].kind == "image"
    # source_url 仍存动作端点 URL（下载用）
    assert rows["截图.png"].source_url.endswith("id=2")


def test_ingest_name_missing_falls_back_to_url(ingest_world: Session) -> None:
    """name 缺失回落 filename_from_url（智齿等自带文件名的 url 仍正常）。"""
    payload = {
        "billId": "BILL-ATT-FB",
        "title": "t",
        "content": "p",
        "attachments": [{"url": "http://k/upload/real.png", "name": None}],
        "_subscribe_callback": {},
    }
    res = KSMIngester(ingest_world).ingest(payload)
    ingest_world.commit()
    row = (
        ingest_world.execute(select(Attachment).where(Attachment.ticket_id == res.ticket_id))
        .scalars()
        .one()
    )
    assert row.filename == "real.png"
    assert row.kind == "image"


def test_ingest_no_attachments_creates_no_rows(ingest_world: Session) -> None:
    payload = {
        "billId": "BILL-ATT-2",
        "title": "t",
        "content": "p",
        "attachment_urls": [],
        "_subscribe_callback": {},
    }
    res = KSMIngester(ingest_world).ingest(payload)
    ingest_world.commit()

    rows = (
        ingest_world.execute(select(Attachment).where(Attachment.ticket_id == res.ticket_id))
        .scalars()
        .all()
    )
    assert rows == []


def test_ingest_missing_attachment_urls_key_creates_no_rows(ingest_world: Session) -> None:
    """payload w/o attachment_urls key at all (older callers) shouldn't blow up."""
    payload = {"billId": "BILL-ATT-3", "title": "t", "content": "p"}
    res = KSMIngester(ingest_world).ingest(payload)
    ingest_world.commit()

    rows = (
        ingest_world.execute(select(Attachment).where(Attachment.ticket_id == res.ticket_id))
        .scalars()
        .all()
    )
    assert rows == []
