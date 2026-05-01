"""Tests for trace context."""

from app.core.trace import ensure_trace_id, get_trace_id, new_trace_id, set_trace_id


def test_new_trace_id_is_16_hex_chars() -> None:
    tid = new_trace_id()
    assert len(tid) == 16
    int(tid, 16)  # raises if not hex


def test_set_and_get_trace_id() -> None:
    set_trace_id("deadbeef" * 2)
    assert get_trace_id() == "deadbeef" * 2
    set_trace_id(None)
    assert get_trace_id() is None


def test_ensure_trace_id_is_idempotent() -> None:
    set_trace_id(None)
    a = ensure_trace_id()
    b = ensure_trace_id()
    assert a == b
    assert len(a) == 16


def test_ensure_trace_id_keeps_existing() -> None:
    set_trace_id("0123456789abcdef")
    assert ensure_trace_id() == "0123456789abcdef"
