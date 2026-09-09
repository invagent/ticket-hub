"""ADR-0017 Task 1.1：stage 派生纯函数表驱动测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.state import stage as st


def _hub(**kw):  # type: ignore[no-untyped-def]
    base = {"type": "Operation", "status": "created", "op_status": None, "linear_status": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _ticket(**kw):  # type: ignore[no-untyped-def]
    base = {"status": "received", "type": "Raw", "predicted_type": None, "hub_issue_id": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_lattice_inclusion() -> None:
    assert st.LINEAR_STAGES < st.HUB_STAGES < st.TICKET_STAGES
    assert set(st.STAGE_ZH) == st.TICKET_STAGES
    assert set(st.STAGE_TONE) == st.TICKET_STAGES
    assert st.TERMINAL_STAGES <= st.HUB_STAGES
    assert st.WAITING_HUMAN_STAGES <= st.TICKET_STAGES


# ---- 无 hub（未毕业） ----------------------------------------------------------


@pytest.mark.parametrize(
    ("ticket_kw", "expected"),
    [
        ({}, "received"),
        ({"status": "in_progress"}, "received"),  # 未毕业其它中间态一律视为已接收
        ({"predicted_type": "Complaint"}, "complaint"),
        ({"predicted_type": "Complaint", "status": "closed"}, "closed"),
        ({"status": "done"}, "resolved"),
        ({"status": "closed"}, "closed"),
        ({"status": "rejected"}, "closed"),
        ({"status": "superseded"}, "closed"),
        ({"status": "transferred_return"}, "returned"),
        ({"status": "split"}, "split"),
        ({"type": "Parent"}, "split"),
    ],
)
def test_ticket_without_hub(ticket_kw, expected) -> None:  # type: ignore[no-untyped-def]
    assert st.derive_ticket_stage(_ticket(**ticket_kw), None) == expected


# ---- 退回最高优先 ---------------------------------------------------------------


def test_returned_beats_everything() -> None:
    hub = _hub(status="released", op_status="answered")
    assert st.derive_ticket_stage(_ticket(status="transferred_return"), hub) == "returned"
    assert st.derive_hub_stage(_hub(status="returned", op_status="answered")) == "returned"
    assert st.derive_hub_stage(_hub(op_status="transferred_return", status="created")) == "returned"


# ---- 闸门 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hub_status", "expected"),
    [
        ("pending_review", "pending_classify"),
        ("pending_linear_review", "pending_push"),
        ("pending", "pending_dispatch"),
    ],
)
def test_gates_beat_op_status(hub_status, expected) -> None:  # type: ignore[no-untyped-def]
    # 毕业时 op_status 已预置 processing，但闸门停摆时必须显示闸门本身
    hub = _hub(status=hub_status, op_status="processing")
    assert st.derive_hub_stage(hub) == expected


# ---- Operation 运营机 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("op_status", "expected"),
    [
        ("processing", "processing"),
        ("reviewing", "pending_answer_review"),
        ("supplementing", "supplementing"),
        ("answered", "answered"),
        ("closed", "closed"),
        ("exception", "exception"),
    ],
)
def test_operation_op_status(op_status, expected) -> None:  # type: ignore[no-untyped-def]
    assert st.derive_hub_stage(_hub(op_status=op_status)) == expected


def test_op_status_answered_beats_hub_resolved() -> None:
    # 答复回写成功会把 hub.status 推到 resolved，但 op_status 独立停在 answered 观察期
    assert st.derive_hub_stage(_hub(status="resolved", op_status="answered")) == "answered"


# ---- hub 终态 / 漏进的 Operation 值 -------------------------------------------------


@pytest.mark.parametrize(
    ("hub_status", "expected"),
    [
        ("resolved", "resolved"),
        ("closed", "closed"),
        ("answered", "answered"),
        ("processing", "processing"),
    ],
)
def test_hub_status_direct(hub_status, expected) -> None:  # type: ignore[no-untyped-def]
    assert st.derive_hub_stage(_hub(type="Bug_fix", status=hub_status)) == expected


# ---- 研发类 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hub_status", "linear_status", "expected"),
    [
        ("created", None, "processing"),  # 已毕业未推
        ("created", "Backlog", "in_dev"),
        ("in_progress", "In Progress", "in_dev"),
        ("in_progress", None, "in_dev"),
        ("in_progress", "In Review", "dev_review"),
        ("released", "Done", "released"),
        ("in_progress", "Done", "released"),  # 回同步延迟：Linear 先 Done
        ("released", None, "released"),
        ("in_progress", "Canceled", "canceled"),
        ("released", "Canceled", "canceled"),  # 取消优先于发版
    ],
)
def test_dev_types(hub_status, linear_status, expected) -> None:  # type: ignore[no-untyped-def]
    for t in ("Bug_fix", "Demand"):
        hub = _hub(type=t, status=hub_status, linear_status=linear_status)
        assert st.derive_hub_stage(hub) == expected, (t, hub_status, linear_status)


def test_internal_task() -> None:
    assert st.derive_hub_stage(_hub(type="Internal_task", status="created")) == "processing"
    assert st.derive_hub_stage(_hub(type="Internal_task", status="released")) == "released"


def test_graduated_ticket_follows_hub() -> None:
    hub = _hub(type="Demand", status="in_progress", linear_status="In Review")
    assert (
        st.derive_ticket_stage(_ticket(status="in_progress", hub_issue_id=1), hub) == "dev_review"
    )


def test_every_derived_value_is_in_lattice() -> None:
    combos = []
    for t in ("Operation", "Bug_fix", "Demand", "Internal_task"):
        for hs in (
            "created",
            "pending_review",
            "pending",
            "in_progress",
            "released",
            "resolved",
            "closed",
            "weird",
        ):
            for op in (None, "processing", "answered", "exception"):
                for lin in (None, "Backlog", "Done", "Canceled", "Custom Col"):
                    combos.append(_hub(type=t, status=hs, op_status=op, linear_status=lin))
    for hub in combos:
        assert st.derive_hub_stage(hub) in st.HUB_STAGES
        assert st.derive_ticket_stage(_ticket(hub_issue_id=1), hub) in st.TICKET_STAGES


def test_stage_label() -> None:
    assert st.stage_label("in_dev") == "研发中"
    assert st.stage_label(None) is None
    assert st.stage_label("unknown_x") == "unknown_x"
