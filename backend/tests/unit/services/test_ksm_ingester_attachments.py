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
