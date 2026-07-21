"""補料回填公共服務單測。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AgentDecision, HubIssue, Ticket
from app.services.ingest.supply_refill import apply_supply_refill


def _seed(
    db: Session, *, reply_v: int = 0, with_hub: bool = True
) -> tuple[Ticket, HubIssue | None]:
    hub = None
    if with_hub:
        hub = HubIssue(
            short_code="HUB-RF-1",
            type="Operation",
            title="開票失敗",
            canonical_body="舊內容",
            status="created",
            reply_content_version=reply_v,
        )
        db.add(hub)
        db.flush()
    t = Ticket(
        short_code="TKT-RF-1",
        source_code="ksm",
        source_ticket_id="bill-1",
        type="Raw",
        status="awaiting_supply",
        source_payload={"billId": "bill-1", "content": "舊內容"},
        title="開票失敗",
        body="開票時提示網絡錯誤",
        hub_issue_id=hub.id if hub else None,
    )
    db.add(t)
    db.flush()
    if hub is not None:
        db.add(
            AgentDecision(
                decision_type="auto_reply",
                subject_type="hub_issue",
                subject_id=hub.id,
                proposal={"branch": "transfer"},
            )
        )
        db.flush()
    return t, hub


def test_refill_updates_content_resets_status_clears_audit(db_session: Session) -> None:
    t, hub = _seed(db_session)
    db_session.commit()
    new_payload = {"billId": "bill-1", "content": "客户补充了报错截图和复现步骤"}
    ok = apply_supply_refill(db_session, t, new_payload)
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert t.source_payload == new_payload
    assert "客户补充了报错截图和复现步骤" in (t.body or "")
    assert "[补料回填" in (t.body or "")
    assert t.status == "received"
    # auto_reply 审计被清 → drain 会重扫
    remaining = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .count()
    )
    assert remaining == 0


def test_refill_preserves_audit_when_already_replied(db_session: Session) -> None:
    """hub 已答复(reply_v>=1)是矛盾状态：只更新内容，不清审计、不复位为可重答。"""
    t, hub = _seed(db_session, reply_v=1)
    db_session.commit()
    ok = apply_supply_refill(db_session, t, {"billId": "bill-1", "content": "新内容"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert "新内容" in (t.body or "")
    # 已答复 → 审计保留（不覆盖已发答复）
    remaining = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .count()
    )
    assert remaining == 1


def test_refill_no_hub_only_updates_content(db_session: Session) -> None:
    t, _ = _seed(db_session, with_hub=False)
    db_session.commit()
    ok = apply_supply_refill(db_session, t, {"billId": "bill-1", "content": "无hub新内容"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert "无hub新内容" in (t.body or "")
    assert t.status == "received"
