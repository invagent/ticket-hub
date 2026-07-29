# KSM 附件闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 KSM 附件闭环——入站同步建 Attachment 行，Celery beat 异步下载→MinIO 转存→vision OCR，OCR 文本回写 ticket.body。

**Architecture:** 复用已有 `VisionClient`（bytes 路径）+ `KSMClient.download_attachment`；新建 MinioStore 存储层 + attachments pipeline 异步流水线；改 ksm_payload（解析附件 url）+ ksm_ingester（建行）；迁移 0022 加 `queued` 状态 + 重试列。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + Celery（Python 3.11+）；MinIO SDK。

## Global Constraints

- 范围仅 KSM；智齿/escalation 存储不动
- 附件字段按参考旧项目：KSM raw 的 `attachment` 数组，每项取 `url`；解析容错（缺失→空列表，不抛）
- 隔离：KSM 附件建行用 `vision_status='queued'`；ingest 链 vision_extract 只扫 `pending`（不改）；异步流水线只扫 `queued`
- 存储用 MinIO SDK（同步，跑在 Celery worker 同步上下文，不用 asyncio.to_thread）
- 灰度：`attachment_pipeline_enabled`(默认false) + `attachment_pipeline_dry_run`(默认true)，镜像 KSM writeback
- MinIO 未配置 / 下载失败 → 标 failed 转人工，**绝不静默成功**
- 幂等：只处理 `vision_status=='queued' AND storage_key IS NULL` 的行；确定性 key 覆盖
- 单条失败 try/except 隔离，不阻塞其他；超 `attachment_max_attempts` 标 failed
- 后端 `make lint`（ruff+mypy）+ `make unit`（≥70%）；改端点 → `make gen-types` + `make check-types`
- 单测 mock MinIO SDK + download + vision（不连真实外部）；集成测试用 testcontainers 标 `@pytest.mark.integration`
- 测试隔离：`tests/conftest.py` 已清空 GLM/DASHSCOPE key

---

## Task 1: 迁移 0022 — queued 状态 + 重试列

**Files:**
- Create: `backend/migrations/versions/0022_attachment_queued.py`
- Modify: `backend/app/models.py:993-996`（CheckConstraint 加 queued）+ `:1000-1018`（加两列）
- Test: `backend/tests/unit/test_migrations_smoke.py`（若存在则加用例；否则靠模型 + 后续任务覆盖）

**Interfaces:**
- Produces: attachments 表新增 `download_attempts: int`（NOT NULL default 0）+ `last_error: str | None`（String512）；`ck_attachments_vision_status` 允许 `queued`；`Attachment.download_attempts` / `Attachment.last_error` ORM 属性

- [ ] **Step 1: 改模型**

`app/models.py`，CheckConstraint（:993-996）改为：

```python
        CheckConstraint(
            "vision_status IN ('pending','queued','extracted','skipped','failed')",
            name="ck_attachments_vision_status",
        ),
```

在 `vision_cost_usd`（:1012）之后加两列：

```python
    download_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 2: 写迁移**

查当前 head：`cd backend && .venv/bin/alembic heads`（应为 0021）。创建 `migrations/versions/0022_attachment_queued.py`，`down_revision="0021"`（以实际 0021 的 revision id 为准——打开 `0021_operation_status_machine.py` 抄它的 `revision` 值作为本迁移的 `down_revision`）：

```python
"""attachment: add queued status + download retry columns

Revision ID: 0022_attachment_queued
Revises: 0021_operation_status_machine
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_attachment_queued"
down_revision = "0021_operation_status_machine"  # 以 0021 文件里的 revision 值为准
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("attachments", sa.Column("last_error", sa.String(512), nullable=True))
    # 升级 CHECK 约束加 'queued'
    op.drop_constraint("ck_attachments_vision_status", "attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_vision_status",
        "attachments",
        "vision_status IN ('pending','queued','extracted','skipped','failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attachments_vision_status", "attachments", type_="check")
    op.create_check_constraint(
        "ck_attachments_vision_status",
        "attachments",
        "vision_status IN ('pending','extracted','skipped','failed')",
    )
    op.drop_column("attachments", "last_error")
    op.drop_column("attachments", "download_attempts")
```

注意：SQLite 不支持 drop/alter CHECK 约束——若单测在 SQLite 跑迁移会失败。本项目单测用 `Base.metadata.create_all`（不跑 alembic），迁移只在 PG 集成/生产跑。确认单测不依赖 alembic upgrade（查 conftest）；若依赖，CHECK 部分用 `batch_alter_table` 包裹。

- [ ] **Step 3: 验证迁移在 PG 可跑（若有 Docker）**

Run: `cd backend && make integration`（或手动 `alembic upgrade head` 对测试 PG）
Expected: 0021 → 0022 升级成功。无 Docker 环境则跳过，靠模型 create_all + 后续任务的 SQLite 单测覆盖字段存在性。

- [ ] **Step 4: 模型层验证字段**

Run: `cd backend && .venv/bin/python -c "from app.models import Attachment; print(Attachment.download_attempts, Attachment.last_error)"`
Expected: 无 AttributeError，打印两个 InstrumentedAttribute。

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/migrations/versions/0022_attachment_queued.py
git commit -m "feat(attachments): 迁移0022 加 queued 状态 + download_attempts/last_error 列"
```

---

## Task 2: MinIO 存储层 + 配置 + 依赖

**Files:**
- Modify: `backend/pyproject.toml`（加 minio 依赖）
- Modify: `backend/app/config.py:36-41`（s3_* → minio_* + 4 个 pipeline 设置）
- Create: `backend/app/core/storage/__init__.py`
- Create: `backend/app/core/storage/minio_store.py`
- Test: `backend/tests/unit/core/storage/test_minio_store.py`

**Interfaces:**
- Consumes: `get_settings()`（`app/config.py`）
- Produces:
  - Settings: `minio_endpoint/minio_access_key/minio_secret_key/minio_bucket/minio_public_base/minio_secure` + `attachment_pipeline_enabled/attachment_pipeline_dry_run/attachment_max_bytes/attachment_max_attempts`
  - `MinioStore(settings)`；`MinioStore.put_bytes(key: str, data: bytes, content_type: str) -> str`（返回 public_url）；`MinioStore.public_url(key: str) -> str`；`MinioStore.ensure_bucket() -> None`
  - `MinioNotConfiguredError`（Exception）
  - `attachment_object_key(ticket_id: int, att_id: int, filename: str | None) -> str`（模块级纯函数，确定性 key）

- [ ] **Step 1: 加依赖**

`pyproject.toml` dependencies 加（httpx 附近）：

```python
    "minio>=7.2.0",
```

Run: `cd backend && make install`（或 `.venv/bin/pip install "minio>=7.2.0"`）
Expected: minio 安装成功。

- [ ] **Step 2: 改配置**

`app/config.py`，把 `s3_endpoint/s3_access_key/s3_secret_key/s3_bucket/s3_region`（:36-41）替换为：

```python
    # 存储（MinIO）
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ticket-hub-attachments"
    minio_public_base: str = ""  # 公开访问前缀(nginx反代)，空则用 endpoint 拼
    minio_secure: bool = False

    # 附件流水线灰度
    attachment_pipeline_enabled: bool = False
    attachment_pipeline_dry_run: bool = True
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_max_attempts: int = 3
```

注意：grep 全项目确认 `s3_endpoint/s3_bucket/s3_access/s3_secret/s3_region` 无其他引用（勘察已确认零引用）；若有则一并改。

- [ ] **Step 3: 写存储层失败测试**

创建 `backend/tests/unit/core/storage/test_minio_store.py`（含 `__init__.py`）。用 `unittest.mock` mock minio SDK：

```python
import pytest
from unittest.mock import MagicMock, patch
from app.config import Settings
from app.core.storage.minio_store import (
    MinioStore,
    MinioNotConfiguredError,
    attachment_object_key,
)


def _settings(**kw):
    base = dict(minio_endpoint="localhost:9000", minio_access_key="k",
                minio_secret_key="s", minio_bucket="b", minio_public_base="",
                minio_secure=False)
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
def test_put_bytes_returns_public_url(MockMinio):
    client = MagicMock()
    MockMinio.return_value = client
    store = MinioStore(_settings(minio_public_base="https://cdn.example.com"))
    url = store.put_bytes("ksm/1/2_a.png", b"data", "image/png")
    assert url == "https://cdn.example.com/ticket-hub-attachments/ksm/1/2_a.png"
    client.put_object.assert_called_once()


@patch("app.core.storage.minio_store.Minio")
def test_public_url_falls_back_to_endpoint(MockMinio):
    store = MinioStore(_settings(minio_public_base="", minio_secure=False))
    url = store.public_url("ksm/1/2_a.png")
    assert "ticket-hub-attachments/ksm/1/2_a.png" in url
    assert url.startswith("http://localhost:9000")
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/core/storage/test_minio_store.py -v`
Expected: FAIL —— ModuleNotFoundError: app.core.storage.minio_store

- [ ] **Step 5: 写存储层实现**

创建 `backend/app/core/storage/__init__.py`（空）+ `backend/app/core/storage/minio_store.py`：

```python
"""MinIO 存储层——附件转存。

MinIO SDK 是同步的；本模块跑在 Celery worker 同步上下文，直接调用即可。
未配置 access/secret key → 构造抛 MinioNotConfiguredError（流水线据此标 failed 转人工，不静默成功）。
"""

from __future__ import annotations

import io
import re
from datetime import timezone  # noqa: F401  (kept for potential future use)

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

    def public_url(self, key: str) -> str:
        if self._public_base:
            return f"{self._public_base}/{self._bucket}/{key}"
        scheme = "https" if self._secure else "http"
        return f"{scheme}://{self._endpoint}/{self._bucket}/{key}"
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/core/storage/test_minio_store.py -v`
Expected: PASS（5 passed）

- [ ] **Step 7: lint**

Run: `cd backend && make lint`
Expected: ruff + mypy clean（新文件）。若 mypy 抱怨 minio 无类型，加 `# type: ignore[import-untyped]` 到 import 行或在 pyproject mypy overrides 加 minio ignore_missing_imports。

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/app/config.py backend/app/core/storage/
git commit -m "feat(storage): MinioStore 存储层 + 附件流水线配置项"
```

---

## Task 3: KSM 附件解析 + 入站建行

**Files:**
- Modify: `backend/app/services/ingest/ksm_payload.py`（加 attachment_urls 解析）
- Modify: `backend/app/services/ingest/ksm_ingester.py:145-180`（flush 后建 Attachment 行）
- Test: `backend/tests/unit/services/test_ksm_payload_attachments.py`
- Test: `backend/tests/unit/services/test_ksm_ingester_attachments.py`

**Interfaces:**
- Consumes: `from_subscribe_callback` 已返回的 payload dict；`Attachment` 模型（含 Task 1 的 queued 状态）；`ticket.id`（flush 后可用）
- Produces:
  - `parse_attachment_urls(data: dict) -> list[str]`（模块级纯函数，`app/services/ingest/ksm_payload.py`）
  - payload dict 新增 key `attachment_urls: list[str]`
  - KSMIngester.ingest 建行：每个 url 一个 `Attachment(ticket_id, source_url=url, kind='image', vision_status='queued')`

- [ ] **Step 1: 写解析失败测试**

创建 `backend/tests/unit/services/test_ksm_payload_attachments.py`：

```python
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
    data = {"billId": "B1", "title": "t", "problem": "p",
            "attachment": [{"url": "http://k/a.png"}]}
    payload = from_subscribe_callback(data)
    assert payload["attachment_urls"] == ["http://k/a.png"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_payload_attachments.py -v`
Expected: FAIL —— ImportError: cannot import name 'parse_attachment_urls'

- [ ] **Step 3: 实现解析**

`app/services/ingest/ksm_payload.py`，加模块级函数（文件顶部或 from_subscribe_callback 之前）：

```python
def parse_attachment_urls(data: dict[str, Any]) -> list[str]:
    """从 KSM subscribeCallback data 块提取附件 url 列表。

    KSM `attachment` 是对象数组，每项含 `url`。容错：缺失/非数组/项无 url
    → 返回空列表，绝不抛（附件字段名按参考旧项目，当前 KSM 版本未确证）。
    """
    raw = data.get("attachment")
    if not isinstance(raw, list):
        return []
    urls: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                urls.append(url.strip())
    return urls
```

在 `from_subscribe_callback` 的 payload dict 里加一行（`_subscribe_callback` 之前）：

```python
        "attachment_urls": parse_attachment_urls(data),
```

- [ ] **Step 4: 跑解析测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_payload_attachments.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 写建行失败测试**

创建 `backend/tests/unit/services/test_ksm_ingester_attachments.py`。参考同目录既有 KSM ingester 测试的 fixture/构造方式（用真实 KSMIngester + in-memory session）：

```python
from app.models import Attachment, Ticket
from sqlalchemy import select


def test_ingest_creates_attachment_rows(db_session, ksm_ingester_factory):
    # ksm_ingester_factory: 复用既有测试里构造 KSMIngester 的方式；
    # 若无该 fixture，直接按既有 test_ksm_ingester.py 的写法构造。
    payload = {
        "billId": "BILL-ATT-1", "title": "报错", "problem": "见附件",
        "attachment_urls": ["http://ksm/a.png", "http://ksm/b.png"],
        "_subscribe_callback": {},
    }
    ingester = ksm_ingester_factory(db_session)
    result = ingester.ingest(payload)
    rows = db_session.execute(
        select(Attachment).where(Attachment.ticket_id == result.ticket_id)
    ).scalars().all()
    assert len(rows) == 2
    assert {r.source_url for r in rows} == {"http://ksm/a.png", "http://ksm/b.png"}
    assert all(r.vision_status == "queued" for r in rows)
    assert all(r.storage_key is None for r in rows)
    assert all(r.kind == "image" for r in rows)


def test_ingest_no_attachments_creates_no_rows(db_session, ksm_ingester_factory):
    payload = {"billId": "BILL-ATT-2", "title": "t", "problem": "p",
               "attachment_urls": [], "_subscribe_callback": {}}
    ingester = ksm_ingester_factory(db_session)
    result = ingester.ingest(payload)
    rows = db_session.execute(
        select(Attachment).where(Attachment.ticket_id == result.ticket_id)
    ).scalars().all()
    assert rows == []
```

注意：`ksm_ingester_factory` 若不存在，打开既有 `tests/unit/services/test_ksm_ingester*.py` 抄它构造 KSMIngester（resolver/router/repos）的方式，内联到测试里。dedup 分支需保证 billId 唯一以走建行路径。

- [ ] **Step 6: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_ingester_attachments.py -v`
Expected: FAIL —— 建行断言失败（rows 为空）

- [ ] **Step 7: 实现建行**

`app/services/ingest/ksm_ingester.py`，在 `self._db.flush()`（约 :180，Route 之后 status history 之前——需 ticket.id）之后加建行逻辑。先确认 flush 位置能拿到 ticket.id，然后插入：

```python
        # 4b. 建附件行（仅建行，不下载；下载+OCR 由异步流水线处理）
        for url in payload.get("attachment_urls", []) or []:
            self._db.add(
                Attachment(
                    ticket_id=ticket.id,
                    source_url=url,
                    kind="image",
                    vision_status="queued",
                )
            )
```

在文件顶部 import 加 `from app.models import Attachment`（若未导入）。注意放在 `ticket.id` 已可用之后（flush 之后）；若现有 flush 在 status history 前，就紧跟 flush 加，再让 status history 照常。

- [ ] **Step 8: 跑建行测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_ingester_attachments.py -v`
Expected: PASS（2 passed）

- [ ] **Step 9: 回归 + lint**

Run: `cd backend && .venv/bin/pytest tests/unit/services/ -q && make lint`
Expected: KSM ingester 既有测试不回归；lint clean

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/ingest/ksm_payload.py backend/app/services/ingest/ksm_ingester.py backend/tests/unit/services/test_ksm_payload_attachments.py backend/tests/unit/services/test_ksm_ingester_attachments.py
git commit -m "feat(ingest): KSM 附件 url 解析 + 入站建 Attachment 行(queued)"
```

---

## Task 4: 异步附件流水线服务

**Files:**
- Create: `backend/app/services/attachments/__init__.py`
- Create: `backend/app/services/attachments/pipeline.py`
- Test: `backend/tests/unit/services/attachments/test_pipeline.py`

**Interfaces:**
- Consumes: `Attachment`（含 queued/download_attempts/last_error）；`MinioStore` + `attachment_object_key`（Task 2）；`KSMClient.download_attachment(url) -> bytes`（`adapters/ksm/client.py:264`）；`VisionClient.extract(prompt, image_bytes=..., mime=...)`（`app/core/llm_router/vision.py:87`）；`get_settings()`
- Produces:
  - `AttachmentDrainReport(scanned:int, stored:int, extracted:int, skipped:int, failed:int)`（dataclass）
  - `drain_pending_attachments(db, *, ksm_client=None, store=None, vision_client=None, limit=20) -> AttachmentDrainReport`
  - `process_one(db, att, *, store, ksm_client, vision_client, settings) -> str`（返回结果状态字符串）

- [ ] **Step 1: 写流水线失败测试**

创建 `backend/tests/unit/services/attachments/test_pipeline.py`（+ `__init__.py`）。全 mock 外部：

```python
import pytest
from unittest.mock import MagicMock
from app.models import Attachment, Ticket
from app.services.attachments.pipeline import (
    drain_pending_attachments,
    AttachmentDrainReport,
)


def _mk_ticket(db):
    t = Ticket(type="Raw", source_code="ksm", source_ticket_id="B1",
               short_code="T-1", title="t", body="orig", status="received")
    db.add(t); db.flush(); return t


def _mk_att(db, ticket_id, **kw):
    base = dict(ticket_id=ticket_id, source_url="http://k/a.png",
                kind="image", vision_status="queued")
    base.update(kw)
    a = Attachment(**base); db.add(a); db.flush(); return a


def _mocks(img=b"PNGDATA", ocr_text="识别文本"):
    ksm = MagicMock(); ksm.download_attachment.return_value = img
    store = MagicMock(); store.put_bytes.return_value = "http://cdn/ksm/1/1_a.png"
    vision = MagicMock()
    vision.extract.return_value = MagicMock(text=ocr_text, model="qwen-vl-max", cost_usd=0.011)
    return ksm, store, vision


def test_happy_path_extracts_and_appends_body(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.extracted == 1 and rep.failed == 0
    db_session.refresh(a); db_session.refresh(t)
    assert a.vision_status == "extracted"
    assert a.storage_key is not None
    assert a.extracted_text == "识别文本"
    assert "识别文本" in t.body  # 追加到 body


def test_dry_run_skips(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=True)
    t = _mk_ticket(db_session); a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.skipped == 1
    ksm.download_attachment.assert_not_called()
    db_session.refresh(a); assert a.vision_status == "skipped"


def test_oversize_skips(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False, max_bytes=3)
    t = _mk_ticket(db_session); a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks(img=b"TOOBIG")
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.skipped == 1
    store.put_bytes.assert_not_called()


def test_download_failure_retries_then_fails(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False, max_attempts=2)
    t = _mk_ticket(db_session); a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    ksm.download_attachment.side_effect = RuntimeError("net")
    # 第一轮：attempts=1，留 queued
    rep1 = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    db_session.refresh(a)
    assert a.download_attempts == 1 and a.vision_status == "queued"
    # 第二轮：attempts=2 达上限 → failed
    rep2 = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    db_session.refresh(a)
    assert a.vision_status == "failed" and a.last_error


def test_only_scans_queued(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    t = _mk_ticket(db_session)
    _mk_att(db_session, t.id, vision_status="pending")   # escalation 用，不该被扫
    _mk_att(db_session, t.id, vision_status="extracted")  # 终态
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.scanned == 0


def test_disabled_noop(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=False, dry_run=False)
    t = _mk_ticket(db_session); _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.scanned == 0  # enabled 关 → 不扫


# helper：monkeypatch settings
def _set_pipeline(mp, *, enabled, dry_run, max_bytes=10*1024*1024, max_attempts=3):
    from app.services.attachments import pipeline as P
    s = MagicMock()
    s.attachment_pipeline_enabled = enabled
    s.attachment_pipeline_dry_run = dry_run
    s.attachment_max_bytes = max_bytes
    s.attachment_max_attempts = max_attempts
    s.vision_model = "qwen-vl-max"
    mp.setattr(P, "get_settings", lambda: s)
```

注意：`VisionClient.extract` 的返回对象字段名以实际为准（勘察显示结构化 `{ocr_text, ui_context, summary}` 或 `.text`——打开 `vision.py:87` 确认真实属性名，测试的 `vision.extract.return_value` 和实现里的读取要对齐）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/attachments/test_pipeline.py -v`
Expected: FAIL —— ModuleNotFoundError

- [ ] **Step 3: 实现流水线**

先读 `app/core/llm_router/vision.py:87-149` 确认 `extract` 签名 + 返回对象属性名，以及现有 `vision_extract.py` 怎么追加 body（`[附件识别]` 段格式）。然后创建 `backend/app/services/attachments/__init__.py`（空）+ `pipeline.py`：

```python
"""异步附件流水线：download → MinIO → vision OCR。

只处理 vision_status=='queued' 的行（KSM 附件；escalation 的 'pending' 归 ingest 链 vision_extract）。
enabled 关 → 整体不扫。dry_run 开 → 只标 skipped 不下载。
单条 try/except 隔离；下载/上传失败 attempts++，超 max_attempts 标 failed。
MinIO 未配置 → 标 failed 转人工，绝不静默成功。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router.vision import VisionClient
from app.core.logging import get_logger
from app.core.storage.minio_store import (
    MinioStore,
    MinioNotConfiguredError,
    attachment_object_key,
)
from app.models import Attachment, Ticket
from adapters.ksm.client import KSMClient  # import 路径以项目实际为准

logger = get_logger(__name__)

_VISION_PROMPT = "识别图片中的报错文本与界面上下文，简述问题。"  # 复用 vision_extract 的 prompt


@dataclass(slots=True)
class AttachmentDrainReport:
    scanned: int = 0
    stored: int = 0
    extracted: int = 0
    skipped: int = 0
    failed: int = 0


def drain_pending_attachments(
    db: Session,
    *,
    ksm_client: KSMClient | None = None,
    store: MinioStore | None = None,
    vision_client: VisionClient | None = None,
    limit: int = 20,
) -> AttachmentDrainReport:
    settings = get_settings()
    report = AttachmentDrainReport()
    if not settings.attachment_pipeline_enabled:
        return report

    rows = (
        db.execute(
            select(Attachment)
            .where(
                and_(
                    Attachment.vision_status == "queued",
                    Attachment.storage_key.is_(None),
                    Attachment.kind == "image",
                    Attachment.source_url.is_not(None),
                )
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    report.scanned = len(rows)
    if not rows:
        return report

    # dry_run 短路：只标 skipped，不建 client
    if settings.attachment_pipeline_dry_run:
        for att in rows:
            att.vision_status = "skipped"
            att.last_error = "dry_run"
            report.skipped += 1
        db.flush()
        return report

    # 惰性建 client（未配置 MinIO → 全批标 failed 转人工，不静默）
    try:
        store = store or MinioStore(settings)
    except MinioNotConfiguredError as e:
        for att in rows:
            att.vision_status = "failed"
            att.last_error = f"minio_not_configured: {e}"
            report.failed += 1
        db.flush()
        logger.error("attachment_pipeline_minio_unconfigured", count=len(rows))
        return report

    ksm_client = ksm_client or KSMClient.from_settings(settings)  # 以实际工厂方法为准
    vision_client = vision_client or VisionClient.from_settings(settings)

    for att in rows:
        status = process_one(
            db, att, store=store, ksm_client=ksm_client,
            vision_client=vision_client, settings=settings,
        )
        if status == "extracted":
            report.extracted += 1
            report.stored += 1
        elif status == "skipped":
            report.skipped += 1
        elif status == "failed":
            report.failed += 1
        # queued（重试留待下轮）不计入终态
    db.flush()
    return report


def process_one(db, att, *, store, ksm_client, vision_client, settings) -> str:
    try:
        img = ksm_client.download_attachment(att.source_url)
        if len(img) > settings.attachment_max_bytes:
            att.vision_status = "skipped"
            att.last_error = f"oversize:{len(img)}"
            return "skipped"

        key = attachment_object_key(att.ticket_id, att.id, att.filename)
        content_type = att.mime or "image/png"
        att.storage_key = store.put_bytes(key, img, content_type)  # public_url 回写 storage_key
        att.size_bytes = len(img)

        result = vision_client.extract(prompt=_VISION_PROMPT, image_bytes=img, mime=content_type)
        text = getattr(result, "text", None) or getattr(result, "ocr_text", None) or ""
        att.extracted_text = text
        att.vision_model = getattr(result, "model", None) or settings.vision_model
        att.vision_cost_usd = getattr(result, "cost_usd", None)
        att.vision_status = "extracted"

        # 追加 OCR 文本到 ticket.body（与 vision_extract 格式一致）
        if text:
            ticket = db.get(Ticket, att.ticket_id)
            if ticket:
                ticket.body = (ticket.body or "") + f"\n\n[附件识别]\n{text}"
        return "extracted"

    except Exception as e:  # noqa: BLE001 — 逐条隔离
        att.download_attempts = (att.download_attempts or 0) + 1
        att.last_error = str(e)[:512]
        if att.download_attempts >= settings.attachment_max_attempts:
            att.vision_status = "failed"
            logger.error("attachment_process_failed", att_id=att.id, error=str(e))
            return "failed"
        logger.warning("attachment_process_retry", att_id=att.id,
                       attempts=att.download_attempts, error=str(e))
        return "queued"
```

注意：`KSMClient.from_settings` / `VisionClient.from_settings` 的确切工厂方法名以实际为准（vision 勘察是 `from_settings`；KSM 查 `adapters/ksm/client.py` 的构造）。若 KSMClient 需 async 或有依赖注入，drain 里的默认构造相应调整——但测试全走注入的 mock，默认构造只在生产 beat 用。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/attachments/test_pipeline.py -v`
Expected: PASS（6 passed）。若 vision 返回对象属性名对不上，改实现的 getattr 顺序 + 测试的 mock 属性一致。

- [ ] **Step 5: lint**

Run: `cd backend && make lint`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/attachments/
git commit -m "feat(attachments): 异步流水线 download→MinIO→OCR（灰度+重试+隔离）"
```

---

## Task 5: Celery beat task + 主管端点

**Files:**
- Create: `backend/app/services/attachments/drain_task.py`
- Modify: `backend/app/celery_app.py`（include + beat_schedule）
- Modify: `backend/app/api/supervisor.py`（drain-attachments 端点，镜像 drain-ksm-writeback at :1011）
- Test: `backend/tests/unit/api/test_supervisor_drain_attachments.py`

**Interfaces:**
- Consumes: `drain_pending_attachments`（Task 4）；`get_session`/`require_supervisor`（deps）
- Produces:
  - Celery task `app.services.attachments.drain_task.drain_attachments`
  - beat entry `drain_attachments_every_5min`
  - `POST /api/supervisor/drain-attachments`（require_supervisor）→ `{scanned, stored, extracted, skipped, failed}`

- [ ] **Step 1: 写 beat task**

创建 `backend/app/services/attachments/drain_task.py`，参考 `app/services/ksm/writeback_task.py` 的 task 结构（建 session、调 service、commit）：

```python
"""Celery beat：定期 drain queued 附件。key/enabled 未配自动跳过（drain 内部已 guard）。"""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.db import SessionLocal  # 以实际 session 工厂为准
from app.services.attachments.pipeline import drain_pending_attachments

logger = get_logger(__name__)


@celery_app.task(name="app.services.attachments.drain_task.drain_attachments")
def drain_attachments() -> dict:
    db = SessionLocal()
    try:
        report = drain_pending_attachments(db)
        db.commit()
        logger.info("drain_attachments_done", scanned=report.scanned,
                    extracted=report.extracted, failed=report.failed)
        return {
            "scanned": report.scanned, "stored": report.stored,
            "extracted": report.extracted, "skipped": report.skipped,
            "failed": report.failed,
        }
    finally:
        db.close()
```

注意：session 工厂名（`SessionLocal` / `get_sessionmaker`）以 `app/db.py` 实际为准；抄 writeback_task.py 的写法。

- [ ] **Step 2: 注册 include + beat**

`app/celery_app.py`：`include` 列表加 `"app.services.attachments.drain_task"`（与现有 zhichi/ksm task 同处）。`beat_schedule` 加（drain_ksm 附近）：

```python
    "drain_attachments_every_5min": {
        "task": "app.services.attachments.drain_task.drain_attachments",
        "schedule": crontab(minute="*/5"),
    },
```

- [ ] **Step 3: 写端点失败测试**

创建 `backend/tests/unit/api/test_supervisor_drain_attachments.py`，参考 `test_supervisor*.py` 的 client/auth fixture：

```python
def test_drain_attachments_requires_supervisor(client, member_auth):
    resp = client.post("/api/supervisor/drain-attachments", headers=member_auth)
    assert resp.status_code == 403


def test_drain_attachments_returns_report(client, supervisor_auth):
    resp = client.post("/api/supervisor/drain-attachments", headers=supervisor_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"scanned", "stored", "extracted", "skipped", "failed"}
```

注意：fixture 名以既有测试实际为准（勘察 Task 1 曾用 `app_client` + `_bearer`）。enabled 默认关 → drain 返回全 0，测试只验结构 + 权限。

- [ ] **Step 4: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_drain_attachments.py -v`
Expected: FAIL —— 404

- [ ] **Step 5: 写端点**

`app/api/supervisor.py`，参考 `drain_ksm_writeback_endpoint`（:1011）。加 schema + endpoint：

```python
class DrainAttachmentsResponse(BaseModel):
    scanned: int
    stored: int
    extracted: int
    skipped: int
    failed: int


@router.post("/drain-attachments", response_model=DrainAttachmentsResponse)
def drain_attachments_endpoint(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DrainAttachmentsResponse:
    from app.services.attachments.pipeline import drain_pending_attachments
    report = drain_pending_attachments(db)
    db.commit()
    logger.info("supervisor_drain_attachments", scanned=report.scanned,
                extracted=report.extracted, operator_user_id=user.user_id)
    return DrainAttachmentsResponse(
        scanned=report.scanned, stored=report.stored,
        extracted=report.extracted, skipped=report.skipped, failed=report.failed,
    )
```

- [ ] **Step 6: 跑端点测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_drain_attachments.py -v`
Expected: PASS（2 passed）

- [ ] **Step 7: lint + gen-types**

Run: `cd backend && make lint`
Expected: clean

Run: `cd .. && make gen-types`
Expected: openapi.json + types.ts 更新（`/api/supervisor/drain-attachments`）

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/attachments/drain_task.py backend/app/celery_app.py backend/app/api/supervisor.py backend/tests/unit/api/test_supervisor_drain_attachments.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(attachments): beat task + 主管 drain-attachments 端点"
```

---

## Task 6: 前端运维面板接 drain-attachments（可选一致性）

**Files:**
- Modify: `frontend/src/pages/workbench/OpsPanel.tsx`（加附件 drain 按钮）

**Interfaces:**
- Consumes: `POST /api/supervisor/drain-attachments`（Task 5）
- Produces: OpsPanel 加一个「Drain 附件流水线」按钮 + 结果显示（scanned/stored/extracted/skipped/failed），镜像现有 KSM/智齿 drain 按钮

- [ ] **Step 1: 读现有 OpsPanel drain 模式**

读 `frontend/src/pages/workbench/OpsPanel.tsx`，找现有 KSM/智齿 drain 按钮的实现（mutation + 内联结果显示 + enabled/dry_run 徽标）。

- [ ] **Step 2: 加附件 drain 按钮**

镜像现有 drain 按钮，加第三个调 `/api/supervisor/drain-attachments`，结果显示 scanned/stored/extracted/skipped/failed。样式与现有一致。

- [ ] **Step 3: type-check + test + build**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/workbench/OpsPanel.tsx
git commit -m "feat(frontend): OpsPanel 接附件流水线 drain 按钮"
```

---

## Task 7: 全量验证 + 收尾

- [ ] **Step 1: 后端全量**

Run: `cd backend && make lint && make unit`
Expected: clean、全绿、覆盖率 ≥70%

- [ ] **Step 2: 类型同步**

Run: `cd .. && make check-types`
Expected: PASS

- [ ] **Step 3: 前端全量**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿

- [ ] **Step 4: 更新记忆**

更新 `frontend_gap_completion.md` 或新建附件闭环记忆：记录完成状态、新模块（MinioStore/pipeline/drain_task）、迁移 0022、灰度开关、⚠️ 上线前需真 payload 验证附件字段名。

- [ ] **Step 5: 最终 commit**

```bash
git add -A && git commit -m "chore: KSM 附件闭环收尾（验证 + 记忆更新）"
```

---

## Self-Review

**Spec coverage:**
- 迁移 queued + 重试列 → Task 1 ✅
- MinIO 存储层 + 配置 + 依赖 → Task 2 ✅
- KSM 附件解析 + 建行 → Task 3 ✅
- 异步流水线（download→store→OCR，灰度/dry_run/重试/隔离/幂等/queued-only）→ Task 4 ✅
- beat + 主管端点 → Task 5 ✅
- 前端 drain 按钮（一致性）→ Task 6 ✅
- 全量验证 → Task 7 ✅
- 保留 vision_extract 不动、绝不静默成功、OCR 追加 body → Task 4 实现 + 约束 ✅

**Placeholder scan:** 无 TBD/TODO。"以实际为准" 注释均指向勘察需最终确认的具体符号（VisionClient.extract 返回属性、session 工厂名、KSMClient 工厂、fixture 名），非逻辑占位——每处都给了确认方法和默认值。

**Type consistency:** `AttachmentDrainReport`/`drain_pending_attachments`/`process_one` 在 Task 4 定义，Task 5 消费，字段一致（scanned/stored/extracted/skipped/failed）；`MinioStore`/`attachment_object_key`/`MinioNotConfiguredError` 在 Task 2 定义，Task 4 消费；`parse_attachment_urls` Task 3 定义即用；`vision_status='queued'` Task 1 迁移 + Task 3 建行 + Task 4 扫描三处一致。

**已知风险（写进 spec，非计划缺陷）：** KSM 附件字段名（`attachment[].url`）未经真实 payload 确证——Task 3 解析写成容错（字段缺失→空），上线前需真 payload 验证。这是有意的降级，不阻塞实现。
