from datetime import UTC, datetime
from decimal import Decimal

from app.models import Ticket
from app.services.metrics.analytics import compute_ticket_analytics


def _tk(db, **kw):
    n = db.query(Ticket).count() + 1
    defaults = {
        "short_code": f"TKT-{n:06d}",
        "source_code": "ksm",
        "source_ticket_id": f"S{n}",
        "type": "Raw",
        "status": "done",
        "received_at": datetime(2026, 4, 1, tzinfo=UTC),
    }
    defaults.update(kw)
    t = Ticket(**defaults)
    db.add(t)
    db.flush()
    return t


def test_kpi_counts_by_type_and_sla(db_session):
    _tk(
        db_session,
        predicted_type="Bug_fix",
        handle_hours=Decimal("6"),
        sla_standard_hours=Decimal("8"),
    )
    _tk(
        db_session,
        predicted_type="Operation",
        handle_hours=Decimal("50"),
        sla_standard_hours=Decimal("40"),
    )
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    assert r.kpi.total == 2
    assert r.kpi.by_type["Bug_fix"] == 1
    assert r.kpi.by_type["Operation"] == 1
    # 1 达标(6<=8), 1 超期(50>40) → sla_rate=0.5
    assert abs(r.kpi.sla_rate - 0.5) < 1e-6


def test_by_product_line_and_assignee(db_session):
    _tk(
        db_session,
        product_line_code="发票云",
        predicted_type="Bug_fix",
        assigned_user_id=None,
        handle_hours=Decimal("4"),
    )
    _tk(
        db_session,
        product_line_code="发票云",
        predicted_type="Operation",
        handle_hours=Decimal("10"),
    )
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    pl = next(x for x in r.by_product_line if x["product_line"] == "发票云")
    assert pl["total"] == 2


def test_trend_by_month(db_session):
    _tk(db_session, received_at=datetime(2026, 4, 15, tzinfo=UTC), handle_hours=Decimal("10"))
    _tk(db_session, received_at=datetime(2026, 5, 15, tzinfo=UTC), handle_hours=Decimal("20"))
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    months = {x["month"] for x in r.trend}
    assert "2026-04" in months and "2026-05" in months
