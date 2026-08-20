"""parse_attachments + from_subscribe_callback attachments wiring（保留真实文件名 name）。"""

from __future__ import annotations

from app.services.ingest.ksm_payload import from_subscribe_callback, parse_attachments


def test_parses_attachment_array_with_name():
    """KSM 附件真实文件名在 name（url 是 accessory!download.action 动作端点）。"""
    data = {
        "attachment": [
            {"name": "报错.docx", "url": "https://ksm/system/accessory!download.action?id=1"},
            {"name": "截图.png", "url": "https://ksm/system/accessory!download.action?id=2"},
        ]
    }
    assert parse_attachments(data) == [
        {"url": "https://ksm/system/accessory!download.action?id=1", "name": "报错.docx"},
        {"url": "https://ksm/system/accessory!download.action?id=2", "name": "截图.png"},
    ]


def test_name_absent_is_none():
    data = {"attachment": [{"url": "http://k/1.png"}]}
    assert parse_attachments(data) == [{"url": "http://k/1.png", "name": None}]


def test_skips_items_without_url():
    data = {"attachment": [{"url": "http://k/1.png", "name": "a"}, {"name": "x"}, {"url": ""}]}
    assert parse_attachments(data) == [{"url": "http://k/1.png", "name": "a"}]


def test_missing_attachment_key_returns_empty():
    assert parse_attachments({}) == []


def test_non_list_attachment_returns_empty():
    assert parse_attachments({"attachment": "nope"}) == []


def test_from_subscribe_callback_includes_attachments():
    data = {
        "billId": "B1",
        "title": "t",
        "problem": "p",
        "attachment": [
            {"name": "a.docx", "url": "https://ksm/system/accessory!download.action?id=9"}
        ],
    }
    payload = from_subscribe_callback(data)
    assert payload["attachments"] == [
        {"url": "https://ksm/system/accessory!download.action?id=9", "name": "a.docx"}
    ]
