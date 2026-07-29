"""parse_attachment_urls + from_subscribe_callback attachment_urls wiring."""

from __future__ import annotations

from app.services.ingest.ksm_payload import parse_attachment_urls


def test_parses_attachment_array():
    data = {"attachment": [{"url": "http://k/1.png"}, {"url": "http://k/2.png"}]}
    assert parse_attachment_urls(data) == ["http://k/1.png", "http://k/2.png"]


def test_skips_items_without_url():
    data = {"attachment": [{"url": "http://k/1.png"}, {"name": "x"}, {"url": ""}]}
    assert parse_attachment_urls(data) == ["http://k/1.png"]


def test_missing_attachment_key_returns_empty():
    assert parse_attachment_urls({}) == []


def test_non_list_attachment_returns_empty():
    assert parse_attachment_urls({"attachment": "nope"}) == []


def test_from_subscribe_callback_includes_attachment_urls():
    from app.services.ingest.ksm_payload import from_subscribe_callback

    data = {
        "billId": "B1",
        "title": "t",
        "problem": "p",
        "attachment": [{"url": "http://k/a.png"}],
    }
    payload = from_subscribe_callback(data)
    assert payload["attachment_urls"] == ["http://k/a.png"]
