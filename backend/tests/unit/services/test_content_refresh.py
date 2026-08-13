"""内容刷新公共服务单测。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket
from app.services.ingest.content_refresh import apply_content_refresh


def _seed(db: Session, *, with_hub: bool = True) -> tuple[Ticket, HubIssue | None]:
    hub = None
    if with_hub:
        hub = HubIssue(
            short_code="HUB-RF-1",
            type="Operation",
            title="开票失败",
            canonical_body="旧内容",
            status="created",
        )
        db.add(hub)
        db.flush()
    t = Ticket(
        short_code="TKT-RF-1",
        source_code="ksm",
        source_ticket_id="bill-1",
        type="Raw",
        status="received",
        source_payload={"billId": "bill-1", "content": "旧内容"},
        title="开票失败",
        body="开票时提示网络错误",
        hub_issue_id=hub.id if hub else None,
    )
    db.add(t)
    db.flush()
    return t, hub


def test_refresh_updates_source_payload_and_body(db_session: Session) -> None:
    t, hub = _seed(db_session)
    db_session.commit()
    new_payload = {"billId": "bill-1", "content": "客户补充了报错截图和复现步骤"}
    ok = apply_content_refresh(db_session, t, new_payload)
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert t.source_payload == new_payload
    assert "客户补充了报错截图和复现步骤" in (t.body or "")
    assert "[内容更新" in (t.body or "")
    # status 不受影响（由调用方 ingester 决定 op_status，不在此判）
    assert t.status == "received"

    db_session.refresh(hub)
    assert "客户补充了报错截图和复现步骤" in (hub.canonical_body or "")
    assert "[内容更新" in (hub.canonical_body or "")


def test_refresh_no_hub_only_updates_ticket(db_session: Session) -> None:
    t, _ = _seed(db_session, with_hub=False)
    db_session.commit()
    ok = apply_content_refresh(db_session, t, {"billId": "bill-1", "content": "无hub新内容"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert "无hub新内容" in (t.body or "")
    assert t.status == "received"


def test_refresh_appends_new_attachments(db_session: Session) -> None:
    """补料重推带的新附件 URL → 建成新 Attachment 行（追加保留，不覆盖历史）。"""
    from app.models import Attachment

    t, _ = _seed(db_session)
    # 首次已有 1 张附件
    db_session.add(
        Attachment(
            ticket_id=t.id, source_url="http://x/old.png", kind="image", vision_status="queued"
        )
    )
    db_session.commit()

    apply_content_refresh(
        db_session,
        t,
        {
            "billId": "bill-1",
            "content": "补充截图",
            "attachment_urls": ["http://x/new1.png", "http://x/new2.jpg"],
        },
    )
    db_session.commit()

    atts = db_session.query(Attachment).filter(Attachment.ticket_id == t.id).all()
    urls = {a.source_url for a in atts}
    assert "http://x/new1.png" in urls and "http://x/new2.jpg" in urls  # 新附件已建
    assert "http://x/old.png" in urls  # 旧附件保留（追加不覆盖）
    assert len(atts) == 3
    # 新建行走附件流水线（queued 待下载/OCR）
    new_row = next(a for a in atts if a.source_url == "http://x/new1.png")
    assert new_row.vision_status == "queued"
    assert new_row.kind == "image"


def test_refresh_no_attachment_urls_builds_nothing(db_session: Session) -> None:
    """new_payload 无 attachment_urls → 不建附件行（幂等：重复重推不堆空行）。"""
    from app.models import Attachment

    t, _ = _seed(db_session)
    db_session.commit()
    apply_content_refresh(db_session, t, {"billId": "bill-1", "content": "纯文字补充"})
    db_session.commit()
    assert db_session.query(Attachment).filter(Attachment.ticket_id == t.id).count() == 0


def test_refresh_empty_content_skips_body_append(db_session: Session) -> None:
    """new_payload 无 content → 不追加 body/canonical_body 段，但 source_payload 仍覆盖。"""
    t, hub = _seed(db_session)
    db_session.commit()
    prev_body = t.body
    prev_canonical = hub.canonical_body
    ok = apply_content_refresh(db_session, t, {"billId": "bill-1"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert t.source_payload == {"billId": "bill-1"}
    assert t.body == prev_body
    db_session.refresh(hub)
    assert hub.canonical_body == prev_canonical
