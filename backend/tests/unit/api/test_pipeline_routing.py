"""ADR-0016 P2c 链路重排：分流决策单测.

不打真实 LLM——monkeypatch triage/split/graduate，断言：
- 非混合按类型分流；Complaint 不毕业
- 混合 + 自动拆关 → 停摆（不毕业）；开 → 拆 + 子单按继承类型分流
- triage 失败 → 不分流
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.agents.triage import SubProblem, TriageResult


def _tri(type_: str, *, is_mixed: bool = False, conf: float = 0.9, subs=()) -> TriageResult:  # type: ignore[no-untyped-def]
    return TriageResult(
        type=type_,
        confidence=conf,
        reason="r",
        is_mixed=is_mixed,
        sub_problems=tuple(subs),
        cost_usd=0.0,
        model="fake",
        raw={},
    )


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Patch the chain's agent calls; record graduate + split invocations."""
    import app.api.webhooks as wh

    calls: dict[str, list] = {"graduate": [], "split": [], "route_child": []}
    monkeypatch.setattr(wh, "extract_ticket_attachments", lambda tid: None)
    monkeypatch.setattr(
        wh, "create_hub_issue_for_ticket_auto", lambda tid: calls["graduate"].append(tid)
    )
    monkeypatch.setattr(wh, "_route_child", lambda cid: calls["route_child"].append(cid))

    def fake_split(tid, executed_by):  # type: ignore[no-untyped-def]
        calls["split"].append(tid)
        return SimpleNamespace(child_ticket_ids=[tid * 10 + 1, tid * 10 + 2])

    monkeypatch.setattr(wh, "execute_split_for_ticket", fake_split)
    return wh, calls


def _set_auto(monkeypatch: pytest.MonkeyPatch, *, hub: bool, split: bool) -> None:
    from app.config import get_settings

    monkeypatch.setenv("HUB_ISSUE_AUTO_ENABLED", "true" if hub else "false")
    monkeypatch.setenv("SPLIT_AUTO_ENABLED", "true" if split else "false")
    monkeypatch.setenv("HUB_ISSUE_AUTO_CONFIDENCE", "0.80")
    monkeypatch.setenv("SPLIT_AUTO_CONFIDENCE", "0.85")
    get_settings.cache_clear()


def test_non_mixed_operation_graduates(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=False)
    monkeypatch.setattr(wh, "run_ticket_triage", lambda tid: _tri("Operation"))
    wh.run_post_ingest_agents(5)
    assert calls["graduate"] == [5]
    assert calls["split"] == []


def test_complaint_does_not_graduate(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=False)
    monkeypatch.setattr(wh, "run_ticket_triage", lambda tid: _tri("Complaint"))
    wh.run_post_ingest_agents(6)
    assert calls["graduate"] == []  # 投诉停 ticket 层


def test_mixed_auto_off_stalls(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=False)  # split_auto 关
    subs = [SubProblem("a", "s", "Bug_fix"), SubProblem("b", "s", "Operation")]
    monkeypatch.setattr(
        wh, "run_ticket_triage", lambda tid: _tri("Bug_fix", is_mixed=True, subs=subs)
    )
    wh.run_post_ingest_agents(7)
    assert calls["split"] == []  # 停摆等人工
    assert calls["graduate"] == []


def test_mixed_auto_on_splits_and_routes_children(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=True)
    subs = [SubProblem("a", "s", "Bug_fix"), SubProblem("b", "s", "Operation")]
    monkeypatch.setattr(
        wh, "run_ticket_triage", lambda tid: _tri("Bug_fix", is_mixed=True, subs=subs)
    )
    wh.run_post_ingest_agents(8)
    assert calls["split"] == [8]
    assert calls["route_child"] == [81, 82]  # 每子单分流
    assert calls["graduate"] == []  # 父单不直接毕业


def test_triage_none_no_routing(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=True)
    monkeypatch.setattr(wh, "run_ticket_triage", lambda tid: None)
    wh.run_post_ingest_agents(9)
    assert calls == {"graduate": [], "split": [], "route_child": []}


def test_low_confidence_no_graduate(spy, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    wh, calls = spy
    _set_auto(monkeypatch, hub=True, split=False)
    monkeypatch.setattr(wh, "run_ticket_triage", lambda tid: _tri("Bug_fix", conf=0.5))
    wh.run_post_ingest_agents(10)
    assert calls["graduate"] == []  # 低于 0.80 门槛
