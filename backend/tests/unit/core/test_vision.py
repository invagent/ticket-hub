"""VisionClient tests — respx-mocked multimodal call + output parsing."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import get_settings
from app.core.llm_router.vision import VisionClient, VisionError, _parse

_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _client() -> VisionClient:
    return VisionClient(api_key="sk-test", model="qwen-vl-max")


def _resp(content: str) -> dict:  # type: ignore[type-arg]
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30},
    }


@respx.mock
def test_extract_parses_structured_output() -> None:
    payload = _resp(
        '{"ocr_text": "提示：商品不支持货物运输模式", "ui_context": "发票云-开票申请页",'
        ' "summary": "开票报错"}'
    )
    route = respx.post(_URL).mock(return_value=httpx.Response(200, json=payload))
    res = _client().extract(prompt="抽取", image_url="https://x/img.png")
    assert res.ocr_text == "提示：商品不支持货物运输模式"
    assert res.ui_context == "发票云-开票申请页"
    assert res.summary == "开票报错"
    assert res.cost_usd > 0
    # request carried the image_url
    sent = route.calls[0].request
    assert b"image_url" in sent.content
    assert b"https://x/img.png" in sent.content


@respx.mock
def test_extract_tolerates_json_fence() -> None:
    payload = _resp('```json\n{"ocr_text": "x", "ui_context": "", "summary": "s"}\n```')
    respx.post(_URL).mock(return_value=httpx.Response(200, json=payload))
    res = _client().extract(prompt="p", image_url="https://x")
    assert res.ocr_text == "x"
    assert res.summary == "s"


@respx.mock
def test_extract_content_as_list_parts() -> None:
    payload = {
        "choices": [
            {"message": {"content": [{"text": '{"ocr_text":"a","ui_context":"b","summary":"c"}'}]}}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }
    respx.post(_URL).mock(return_value=httpx.Response(200, json=payload))
    res = _client().extract(prompt="p", image_url="https://x")
    assert res.ocr_text == "a" and res.ui_context == "b"


@respx.mock
def test_extract_base64_data_url() -> None:
    route = respx.post(_URL).mock(
        return_value=httpx.Response(
            200, json=_resp('{"ocr_text":"","ui_context":"","summary":"s"}')
        )
    )
    _client().extract(prompt="p", image_bytes=b"\x89PNG\r\n", mime="image/png")
    assert b"data:image/png;base64," in route.calls[0].request.content


@respx.mock
def test_extract_http_error_raises() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(VisionError, match="HTTP 500"):
        _client().extract(prompt="p", image_url="https://x")


@respx.mock
def test_extract_non_json_raises() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_resp("我看到一张报错图")))
    with pytest.raises(VisionError, match="non-JSON"):
        _client().extract(prompt="p", image_url="https://x")


def test_extract_needs_an_image() -> None:
    with pytest.raises(VisionError, match="needs image_url or image_bytes"):
        _client().extract(prompt="p")


def test_as_body_block_renders() -> None:
    payload = _resp('{"ocr_text": "报错X", "ui_context": "界面Y", "summary": "概括Z"}')
    with respx.mock:
        respx.post(_URL).mock(return_value=httpx.Response(200, json=payload))
        res = _client().extract(prompt="p", image_url="https://x")
    block = res.as_body_block()
    assert block.startswith("[附件识别]")
    assert "概括Z" in block and "报错X" in block and "界面Y" in block


def test_parse_extracts_embedded_json() -> None:
    out = _parse('前言 {"ocr_text": "a", "ui_context": "b", "summary": "c"} 尾巴')
    assert out == {"ocr_text": "a", "ui_context": "b", "summary": "c"}


def test_from_settings_falls_back_to_dashscope_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ds")
    monkeypatch.setenv("VISION_MODEL", "qwen-vl-plus")
    get_settings.cache_clear()
    try:
        c = VisionClient.from_settings()
        assert c.api_key == "sk-ds"
        assert c.model == "qwen-vl-plus"
    finally:
        get_settings.cache_clear()


def test_from_settings_no_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    with pytest.raises(VisionError, match="no vision API key"):
        VisionClient.from_settings()
    get_settings.cache_clear()
