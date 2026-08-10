"""MinIO 存储层——附件转存。

MinIO SDK 是同步的；本模块跑在 Celery worker 同步上下文，直接调用即可。
未配置 access/secret key → 构造抛 MinioNotConfiguredError（流水线据此标 failed 转人工，不静默成功）。
"""

from __future__ import annotations

import io
import re

from minio import Minio

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MinioNotConfiguredError(Exception):
    """MinIO access/secret key 未配置。"""


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def attachment_object_key(ticket_id: int, att_id: int, filename: str | None) -> str:
    """确定性对象 key：ksm/{ticket_id}/{att_id}_{safe_filename}。

    同一附件重跑覆盖同 key（幂等）。filename 缺失用 att_id 兜底。
    """
    safe = _SAFE.sub("_", filename).strip("_") if filename else ""
    suffix = f"_{safe}" if safe else ""
    return f"ksm/{ticket_id}/{att_id}{suffix}"


class MinioStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.minio_access_key or not settings.minio_secret_key:
            raise MinioNotConfiguredError("MinIO access/secret key 未配置")
        self._bucket = settings.minio_bucket
        self._public_base = settings.minio_public_base.rstrip("/")
        self._endpoint = settings.minio_endpoint
        self._secure = settings.minio_secure
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.ensure_bucket()
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        logger.info("minio_put", bucket=self._bucket, key=key, size=len(data))
        return self.public_url(key)

    def get_bytes(self, key: str) -> bytes:
        """按对象 key 读回附件字节（下载代理端点用；不存在则 minio 抛错）。"""
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def key_from_storage_url(self, storage_key: str) -> str | None:
        """从落库的 storage_key（put_bytes 返回的完整 URL）还原 MinIO 对象 key。

        storage_key 形如 `{base}/{bucket}/{object_key}`；截 `/{bucket}/` 之后的部分。
        非本 bucket 的 URL（历史脏数据/外部直链）返回 None，调用方回落 source_url。
        """
        marker = f"/{self._bucket}/"
        idx = storage_key.find(marker)
        if idx < 0:
            return None
        return storage_key[idx + len(marker) :]

    def public_url(self, key: str) -> str:
        if self._public_base:
            return f"{self._public_base}/{self._bucket}/{key}"
        scheme = "https" if self._secure else "http"
        return f"{scheme}://{self._endpoint}/{self._bucket}/{key}"
