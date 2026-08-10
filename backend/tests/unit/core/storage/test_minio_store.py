from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.core.storage.minio_store import (
    MinioNotConfiguredError,
    MinioStore,
    attachment_object_key,
    classify_attachment_kind,
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
        ("https://x/logs.zip", "other"),
        ("https://x/server.log", "other"),
        ("https://x/data.csv", "other"),
        ("https://x/notes.txt", "other"),
        ("https://x/archive.tar.gz", "other"),
        ("https://x/no_ext_here", "other"),
        ("https://x/a.jpg?sign=abc&t=1", "image"),  # 忽略 query
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_attachment_kind(url, expected):
    assert classify_attachment_kind(url) == expected


@patch("app.core.storage.minio_store.Minio")
def test_key_from_storage_url(mock_minio):
    store = MinioStore(_settings(minio_public_base=""))
    sk = "http://localhost:9000/ticket-hub-attachments/ksm/5951/1"
    assert store.key_from_storage_url(sk) == "ksm/5951/1"
    # 非本 bucket 的 URL → None（回落 source_url）
    assert store.key_from_storage_url("https://other.com/foo/bar") is None
