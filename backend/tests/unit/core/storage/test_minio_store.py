from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.core.storage.minio_store import (
    MinioNotConfiguredError,
    MinioStore,
    attachment_object_key,
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
