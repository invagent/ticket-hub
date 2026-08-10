from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.core.storage.minio_store import (
    MinioNotConfiguredError,
    MinioStore,
    attachment_object_key,
    classify_attachment_kind,
    filename_from_url,
    guess_content_type,
)


def _settings(**kw):
    base = {
        "minio_endpoint": "localhost:9000",
        "minio_access_key": "k",
        "minio_secret_key": "s",
        "minio_bucket": "ticket-hub-attachments",
        "minio_public_base": "",
        "minio_secure": False,
    }
    base.update(kw)
    return Settings(**base)


def test_object_key_deterministic():
    k1 = attachment_object_key(12, 34, "err.png")
    k2 = attachment_object_key(12, 34, "err.png")
    assert k1 == k2
    assert k1.startswith("ksm/12/34_")
    assert k1.endswith(".png") or "err" in k1


def test_object_key_handles_none_filename():
    k = attachment_object_key(1, 2, None)
    assert k.startswith("ksm/1/2")


def test_not_configured_raises():
    with pytest.raises(MinioNotConfiguredError):
        MinioStore(_settings(minio_access_key="", minio_secret_key=""))


@patch("app.core.storage.minio_store.Minio")
def test_put_bytes_returns_public_url(mock_minio):
    client = MagicMock()
    mock_minio.return_value = client
    store = MinioStore(_settings(minio_public_base="https://cdn.example.com"))
    url = store.put_bytes("ksm/1/2_a.png", b"data", "image/png")
    assert url == "https://cdn.example.com/ticket-hub-attachments/ksm/1/2_a.png"
    client.put_object.assert_called_once()


@patch("app.core.storage.minio_store.Minio")
def test_public_url_falls_back_to_endpoint(mock_minio):
    store = MinioStore(_settings(minio_public_base="", minio_secure=False))
    url = store.public_url("ksm/1/2_a.png")
    assert "ticket-hub-attachments/ksm/1/2_a.png" in url
    assert url.startswith("http://localhost:9000")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://img.sobot.com/x/OGX1u_123.jpg", "image"),
        ("http://k/a.PNG", "image"),
        ("https://x/screenshot.webp", "image"),
        ("https://x/report.pdf", "pdf"),
        ("https://x/demo.mp4", "video"),
        # 用户清单里非图片/pdf/video 的都归 other（前端再按扩展名细分查看方式）
        ("https://x/logs.zip", "other"),
        ("https://x/server.log", "other"),
        ("https://x/invoice.ofd", "other"),
        ("https://x/data.xml", "other"),
        ("https://x/notes.txt", "other"),
        ("https://x/report.doc", "other"),
        ("https://x/report.docx", "other"),
        ("https://x/sheet.xls", "other"),
        ("https://x/sheet.xlsx", "other"),
        ("https://x/slides.ppt", "other"),
        ("https://x/slides.pptx", "other"),
        ("https://x/no_ext_here", "other"),
        ("https://x/a.jpg?sign=abc&t=1", "image"),  # 忽略 query
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_attachment_kind(url, expected):
    assert classify_attachment_kind(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://img.sobot.com/a/OGX1u_1786328048047.jpg", "OGX1u_1786328048047.jpg"),
        ("https://x/report.pdf?sign=abc", "report.pdf"),  # 去 query
        ("https://x/%E5%8F%91%E7%A5%A8.ofd", "发票.ofd"),  # URL-decode
        ("https://x/dir/", None),  # 末段空
        (None, None),
        ("", None),
    ],
)
def test_filename_from_url(url, expected):
    assert filename_from_url(url) == expected


@pytest.mark.parametrize(
    "filename,kind,expected",
    [
        ("server.log", "other", "text/plain; charset=utf-8"),  # log → 文本(在线看)
        ("notes.txt", "other", "text/plain; charset=utf-8"),
        ("data.xml", "other", "application/xml; charset=utf-8"),
        ("invoice.ofd", "other", "application/ofd"),  # ofd 自定义
        ("report.pdf", "pdf", "application/pdf"),  # mimetypes
        ("shot.png", "image", "image/png"),
        ("clip.mp4", "video", "video/mp4"),
    ],
)
def test_guess_content_type_by_ext(filename, kind, expected):
    assert guess_content_type(filename=filename, source_url=None, kind=kind) == expected


def test_guess_content_type_magic_fallback():
    # 无扩展名信息，靠字节 magic
    assert guess_content_type(filename=None, source_url=None, kind="image", data=b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert guess_content_type(filename=None, source_url=None, kind="other", data=b"%PDF-1.7") == "application/pdf"
    # 全无 → kind 兜底
    assert guess_content_type(filename=None, source_url=None, kind="image") == "image/png"
    assert guess_content_type(filename=None, source_url=None, kind="other") == "application/octet-stream"


@patch("app.core.storage.minio_store.Minio")
def test_key_from_storage_url(mock_minio):
    store = MinioStore(_settings(minio_public_base=""))
    sk = "http://localhost:9000/ticket-hub-attachments/ksm/5951/1"
    assert store.key_from_storage_url(sk) == "ksm/5951/1"
    # 非本 bucket 的 URL → None（回落 source_url）
    assert store.key_from_storage_url("https://other.com/foo/bar") is None
