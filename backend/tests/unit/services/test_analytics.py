from datetime import UTC, datetime
from decimal import Decimal

from app.models import Ticket, User
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
    # 1 达标(6<=8), 1 超期(50>40) → sla_rate=0.5, 分母 sla_base=2
    assert abs(r.kpi.sla_rate - 0.5) < 1e-6
    assert r.kpi.sla_base == 2
    # 两条都未设 assigned_user_id → 未分配数=2
    assert r.kpi.unassigned_count == 2
    assert r.kpi.unassigned_avg_hours is not None


def test_by_module_and_assignee(db_session):
    _tk(
        db_session,
        module="开票管理",
        predicted_type="Bug_fix",
        assigned_user_id=None,
        handle_hours=Decimal("4"),
    )
    _tk(
        db_session,
        module="开票管理",
        predicted_type="Operation",
        handle_hours=Decimal("10"),
    )
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    m = next(x for x in r.by_module if x["module"] == "开票管理")
    assert m["total"] == 2


def test_by_module_overdue_count(db_session):
    # 一条超期(50>40) + 一条达标(6<=8)，同一模块
    _tk(
        db_session,
        module="收票管理",
        predicted_type="Bug_fix",
        handle_hours=Decimal("50"),
        sla_standard_hours=Decimal("40"),
    )
    _tk(
        db_session,
        module="收票管理",
        predicted_type="Bug_fix",
        handle_hours=Decimal("6"),
        sla_standard_hours=Decimal("8"),
    )
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    m = next(x for x in r.by_module if x["module"] == "收票管理")
    assert m["total"] == 2
    assert m["overdue_count"] == 1


def test_trend_by_month(db_session):
    _tk(db_session, received_at=datetime(2026, 4, 15, tzinfo=UTC), handle_hours=Decimal("10"))
    _tk(db_session, received_at=datetime(2026, 5, 15, tzinfo=UTC), handle_hours=Decimal("20"))
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    months = {x["month"] for x in r.trend}
    assert "2026-04" in months and "2026-05" in months
    # available_months 降序，含全部有工单的月份
    assert r.available_months == ["2026-05", "2026-04"]


def test_by_dev_staff(db_session):
    # 研发人员 A：2 Bug + 1 需求，耗时 [4, 8, 12] → 中位 8
    _tk(db_session, predicted_type="Bug_fix", assigned_user_id=1, handle_hours=Decimal("4"))
    _tk(db_session, predicted_type="Bug_fix", assigned_user_id=1, handle_hours=Decimal("8"))
    _tk(db_session, predicted_type="Demand", assigned_user_id=1, handle_hours=Decimal("12"))
    # 研发人员 B：1 需求
    _tk(db_session, predicted_type="Demand", assigned_user_id=2, handle_hours=Decimal("20"))
    # Operation 不算研发，不进 by_dev_staff
    _tk(db_session, predicted_type="Operation", assigned_user_id=1, handle_hours=Decimal("99"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="研发甲", role="assignee"))
    db_session.add(User(id=2, feishu_uid="ou_b", name="研发乙", role="assignee"))
    db_session.commit()

    r = compute_ticket_analytics(db_session)
    a = next(x for x in r.by_dev_staff if x["user_id"] == 1)
    assert a["total"] == 3  # 不含 Operation
    assert a["by_type"]["Bug_fix"] == 2
    assert a["by_type"]["Demand"] == 1
    assert abs(a["median_handle_hours"] - 8.0) < 1e-6  # [4,8,12] 中位
    assert abs(a["avg_handle_hours"] - 8.0) < 1e-6  # (4+8+12)/3
    # 按 total 降序：甲(3) 在 乙(1) 前
    dev_ids = [x["user_id"] for x in r.by_dev_staff]
    assert dev_ids.index(1) < dev_ids.index(2)
