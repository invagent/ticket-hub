# 智齿双向打通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复智齿入站字段映射（真实三层信封格式）+ 新建智齿出站回写（adapters/zhichi + writeback），让智齿工单能完整收进来、主管回复能回到智齿客户，最后部署 SIT。

**Architecture:** 入站改 `ZhichiIngester` 解析 `{source, raw, fields}` 信封（fields 中文块主源、raw 兜底）。出站镜像 KSM 模式：`adapters/zhichi/` 纯 HTTP client（get_token 签名 + save_ticket_reply + get_data_dict 查坐席）+ `app/services/zhichi/writeback.py` 消费 `sync_outbox` 中 `target_source_code='zhichi'` 的 pending 行。灰度阀镜像 KSM（enabled/dry_run 默认关）。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / httpx / Celery / pytest。参考蓝本 `backend/adapters/ksm/` + `backend/app/services/ksm/writeback.py`。

## Global Constraints

- Python `>=3.11,<3.14`；FastAPI 固定 `0.115.14`。
- 智齿 base url：`https://www.soboten.com`（config 默认，可覆盖）。
- 智齿成功判断：响应体 `ret_code == "000000"`，否则取 `ret_msg` 报错。
- 鉴权：`GET /api/get_token?appid=&create_time=&sign=`，`sign = md5(appid + create_time + app_key)`（create_time 秒级时间戳字符串）；业务接口 header 带 `token`。
- 出站 kind→ticket_status：reply/status released/release_note → `3`(已解决/关单)；supply/progress_note → `2`(等待回复/不关单)；status in_progress → skip。
- 坐席：工单 `raw.deal_agent_name` → get_data_dict 查 agentid；为空用 `zhichi_fallback_agent_name`（默认"莉莉"）；查不到 → failed 转人工（不静默跳过）。
- 灰度：`zhichi_writeback_enabled`(默认 False) + `zhichi_writeback_dry_run`(默认 True)。
- 单测沿用 `conftest.py` 清空 provider key（智齿 appid/app_key 测试环境留空）。
- 提交频繁，每个 Task 末尾 commit。分支 `feat/zhichi-integration`（已建，spec 已提交）。

---

### Task 1: config 新增智齿出站配置项

**Files:**
- Modify: `backend/app/config.py`（Zhichi 段，约 :77-79）
- Modify: `backend/deploy/.env.sit.example`（补出站配置）
- Test: `backend/tests/unit/test_config.py`（若无则内联断言）

**Interfaces:**
- Produces: `settings.zhichi_base_url` / `zhichi_writeback_enabled` / `zhichi_writeback_dry_run` / `zhichi_writeback_batch` / `zhichi_writeback_max_attempts` / `zhichi_fallback_agent_name`（供 Task 4/5/6 用）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/test_config.py （新增或追加）
from app.config import Settings

def test_zhichi_writeback_defaults():
    s = Settings()
    assert s.zhichi_base_url == "https://www.soboten.com"
    assert s.zhichi_writeback_enabled is False
    assert s.zhichi_writeback_dry_run is True
    assert s.zhichi_writeback_batch == 20
    assert s.zhichi_writeback_max_attempts == 5
    assert s.zhichi_fallback_agent_name == "莉莉"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py::test_zhichi_writeback_defaults -v`
Expected: FAIL（AttributeError: zhichi_base_url）

- [ ] **Step 3: 加配置项**

`backend/app/config.py` 在 `zhichi_app_key` 行后补：

```python
    # ---- 智齿出站回写 sender（消费 sync_outbox，镜像 KSM 灰度剧本）----
    zhichi_base_url: str = "https://www.soboten.com"
    zhichi_writeback_enabled: bool = False  # 总开关：关时 drain 直接跳过
    zhichi_writeback_dry_run: bool = True  # 只组装标 skipped，不真发
    zhichi_writeback_batch: int = 20
    zhichi_writeback_max_attempts: int = 5
    zhichi_fallback_agent_name: str = "莉莉"  # deal_agent_name 空时的默认坐席
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py::test_zhichi_writeback_defaults -v`
Expected: PASS

- [ ] **Step 5: 补 .env.sit.example**

`backend/deploy/.env.sit.example` 的 Zhichi 段（若无则在 Linear 段后加）：

```
# ---- 智齿出站回写（默认关 + dry_run，配好 appid/app_key 后灰度）----
ZHICHI_APPID=
ZHICHI_APP_KEY=
ZHICHI_BASE_URL=https://www.soboten.com
ZHICHI_WRITEBACK_ENABLED=false
ZHICHI_WRITEBACK_DRY_RUN=true
ZHICHI_FALLBACK_AGENT_NAME=莉莉
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py backend/deploy/.env.sit.example
git commit -m "feat(zhichi): 出站回写配置项 + .env 模板"
```

---

### ⚠️ Task 2 + Task 3 已存在（复用，不重建）

**执行时发现**：`backend/adapters/zhichi/`（client.py/types.py/exceptions.py/__init__.py）已完整实现，9 个测试全绿。现有 API 与计划等价但命名不同：
- `ZhichiConfig(appid=, app_key=, base_url=)`（字段 `appid` 非 `app_id`；已默认 soboten.com）
- `ReplyTicketRequest(ticket_id, ticket_title, ticket_content, ticket_status, ticket_level, reply_agentid, reply_agent_name, reply_content="", reply_type="0", reply_file_str="")`
- `ZhichiClient.reply_ticket(req)`（= save_ticket_reply）、`get_agent_by_name(name) -> Agent|None`（查不到返回 None）、`list_agents`、`get_ticket_by_id`、`upload_file`
- 异常 `ZhichiError`/`ZhichiAuthError`/`ZhichiBusinessError(op, ret_code, ret_msg)`

**Task 5 按现有 API 适配**（下方已改）。Task 2/3 跳过。

### ~~Task 2: adapters/zhichi types + exceptions~~（已存在，跳过）

**Files:**
- Create: `backend/adapters/zhichi/__init__.py`
- Create: `backend/adapters/zhichi/types.py`
- Create: `backend/adapters/zhichi/exceptions.py`
- Test: `backend/tests/unit/adapters/test_zhichi_types.py`

**Interfaces:**
- Produces: `ZhichiConfig`（from_settings）、`ReplyTicketRequest` DTO、`ZhichiError`/`ZhichiBusinessError`/`ZhichiAuthError`（供 Task 3/5）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/adapters/test_zhichi_types.py
from adapters.zhichi import ZhichiConfig, ReplyTicketRequest

class _S:
    zhichi_appid = "app1"
    zhichi_app_key = "key1"
    zhichi_base_url = "https://www.soboten.com"
    zhichi_fallback_agent_name = "莉莉"

def test_config_from_settings():
    c = ZhichiConfig.from_settings(_S())
    assert c.app_id == "app1"
    assert c.app_key == "key1"
    assert c.base_url == "https://www.soboten.com"
    assert c.fallback_agent_name == "莉莉"

def test_reply_request_fields():
    r = ReplyTicketRequest(
        ticketid="T1", ticket_title="标题", ticket_content="正文",
        reply_content="回复", reply_agentid="a1", reply_agent_name="莉莉",
        ticket_status="3", ticket_level="1",
    )
    assert r.reply_type == "0"  # 默认所有人可见
    assert r.reply_file_str is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/adapters/test_zhichi_types.py -v`
Expected: FAIL（ModuleNotFoundError: adapters.zhichi）

- [ ] **Step 3: 建 exceptions.py**

```python
# backend/adapters/zhichi/exceptions.py
"""智齿 adapter 异常。"""
from __future__ import annotations


class ZhichiError(Exception):
    """智齿接口调用失败基类。"""


class ZhichiAuthError(ZhichiError):
    """token 获取/失效。"""


class ZhichiBusinessError(ZhichiError):
    """ret_code != '000000'。"""
```

- [ ] **Step 4: 建 types.py**

```python
# backend/adapters/zhichi/types.py
"""智齿 adapter DTOs。原生接口 https://developer.zhichi.com/pages/950d89/。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ZhichiConfig:
    app_id: str
    app_key: str
    base_url: str = "https://www.soboten.com"
    timeout_seconds: float = 60.0
    fallback_agent_name: str = "莉莉"

    @classmethod
    def from_settings(cls, s: Any) -> ZhichiConfig:
        return cls(
            app_id=getattr(s, "zhichi_appid", ""),
            app_key=getattr(s, "zhichi_app_key", ""),
            base_url=(getattr(s, "zhichi_base_url", "") or "https://www.soboten.com").rstrip("/"),
            fallback_agent_name=getattr(s, "zhichi_fallback_agent_name", "") or "莉莉",
        )


@dataclass(slots=True, frozen=True)
class ReplyTicketRequest:
    """POST /api/ws/5/ticket/save_ticket_reply 入参。"""

    ticketid: str
    ticket_title: str
    ticket_content: str
    reply_content: str
    reply_agentid: str
    reply_agent_name: str
    ticket_status: str  # 0未受理/1受理中/2等待回复/3已解决/99已关闭
    ticket_level: str  # 0低/1中/2高/3紧急
    reply_type: str = "0"  # 0所有人可见/1仅坐席
    reply_file_str: str | None = None
```

- [ ] **Step 5: 建 __init__.py**

```python
# backend/adapters/zhichi/__init__.py
"""智齿（Sobot）adapter — 出站工单回复。"""
from .client import ZhichiClient
from .exceptions import ZhichiAuthError, ZhichiBusinessError, ZhichiError
from .types import ReplyTicketRequest, ZhichiConfig

__all__ = [
    "ReplyTicketRequest",
    "ZhichiAuthError",
    "ZhichiBusinessError",
    "ZhichiClient",
    "ZhichiConfig",
    "ZhichiError",
]
```

> 注：`__init__.py` 引用了 Task 3 的 `ZhichiClient`。本 Task 先建 client.py 空壳（`class ZhichiClient: ...`）让 import 通过，Task 3 填实现。或调整 Task 顺序先建 client 骨架。为让本 Task 测试独立通过，Step 5 后加空壳：

```python
# backend/adapters/zhichi/client.py （本 Task 仅建空壳，Task 3 实现）
"""智齿 HTTP 客户端。"""
from __future__ import annotations


class ZhichiClient:  # Task 3 填充
    ...
```

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/adapters/test_zhichi_types.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/adapters/zhichi/ backend/tests/unit/adapters/test_zhichi_types.py
git commit -m "feat(zhichi): adapter types + exceptions"
```

---

### Task 3: ZhichiClient（鉴权 + save_ticket_reply + get_data_dict）

**Files:**
- Modify: `backend/adapters/zhichi/client.py`（填充 Task 2 的空壳）
- Test: `backend/tests/unit/adapters/test_zhichi_client.py`

**Interfaces:**
- Consumes: `ZhichiConfig` / `ReplyTicketRequest` / 异常（Task 2）；`adapters._token_cache.TokenCache`
- Produces: `ZhichiClient(config, http_client=None)`，方法 `get_data_dict() -> list[dict]`、`resolve_agent(name: str) -> str`(返回 agentid，查不到抛 ZhichiError)、`save_ticket_reply(req: ReplyTicketRequest) -> dict`、`close()`（供 Task 5）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/adapters/test_zhichi_client.py
import httpx
import pytest
from adapters.zhichi import ZhichiClient, ZhichiConfig, ReplyTicketRequest, ZhichiError

_CFG = ZhichiConfig(app_id="1", app_key="2", base_url="https://www.soboten.com")


def _client(handler):
    transport = httpx.MockTransport(handler)
    return ZhichiClient(_CFG, http_client=httpx.Client(transport=transport))


def test_get_token_sign_and_reply():
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get_token":
            calls["token_qs"] = dict(request.url.params)
            return httpx.Response(200, json={"ret_code": "000000", "ret_msg": "ok",
                                             "item": {"token": "TK", "expires_in": "86400"}})
        if request.url.path == "/api/ws/5/ticket/save_ticket_reply":
            calls["reply_token"] = request.headers.get("token")
            return httpx.Response(200, json={"ret_code": "000000", "ret_msg": "操作成功"})
        return httpx.Response(404)

    c = _client(handler)
    req = ReplyTicketRequest(ticketid="T1", ticket_title="t", ticket_content="c",
                             reply_content="r", reply_agentid="a1", reply_agent_name="莉莉",
                             ticket_status="3", ticket_level="1")
    result = c.save_ticket_reply(req)
    assert result["ret_code"] == "000000"
    assert calls["reply_token"] == "TK"
    # sign = md5(appid + create_time + app_key)，验证 appid 传对
    assert calls["token_qs"]["appid"] == "1"
    c.close()


def test_resolve_agent():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get_token":
            return httpx.Response(200, json={"ret_code": "000000", "item": {"token": "TK", "expires_in": "86400"}})
        if request.url.path == "/api/ws/5/ticket/get_data_dict":
            return httpx.Response(200, json={"ret_code": "000000", "item": {
                "agent_list": [{"agentid": "a1", "agent_name": "莉莉"}, {"agentid": "a2", "agent_name": "张三"}]}})
        return httpx.Response(404)

    c = _client(handler)
    assert c.resolve_agent("莉莉") == "a1"
    with pytest.raises(ZhichiError):
        c.resolve_agent("查无此人")
    c.close()


def test_business_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get_token":
            return httpx.Response(200, json={"ret_code": "000000", "item": {"token": "TK", "expires_in": "86400"}})
        return httpx.Response(200, json={"ret_code": "999999", "ret_msg": "业务失败"})

    c = _client(handler)
    req = ReplyTicketRequest(ticketid="T1", ticket_title="t", ticket_content="c",
                             reply_content="r", reply_agentid="a1", reply_agent_name="莉莉",
                             ticket_status="3", ticket_level="1")
    with pytest.raises(ZhichiError):
        c.save_ticket_reply(req)
    c.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/adapters/test_zhichi_client.py -v`
Expected: FAIL（ZhichiClient 无 save_ticket_reply）

- [ ] **Step 3: 实现 client.py**

```python
# backend/adapters/zhichi/client.py
"""智齿（Sobot）HTTP 客户端 — 出站工单回复。

原生接口 https://developer.zhichi.com/pages/950d89/：
  * get_token 签名 md5(appid+create_time+app_key)，token header 带；
  * 业务成功判断 ret_code == "000000"；
  * get_data_dict 拿 agent_list 查坐席 agentid（缓存）。
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from adapters._token_cache import TokenCache
from adapters.zhichi.exceptions import ZhichiBusinessError, ZhichiError
from adapters.zhichi.types import ReplyTicketRequest, ZhichiConfig

_TOKEN_TTL_FALLBACK = 86400.0
_AGENT_CACHE_TTL = 30 * 60  # 坐席列表缓存 30 分钟
_TOKEN_INVALID_CODES = {"100001", "100002"}


class ZhichiClient:
    def __init__(self, config: ZhichiConfig, *, http_client: httpx.Client | None = None) -> None:
        self._cfg = config
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)
        self._token_cache = TokenCache(name="zhichi.token")
        self._agents: list[dict[str, Any]] | None = None
        self._agents_at: float = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ZhichiClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- auth ----
    def _refresh_token(self) -> tuple[str, float]:
        create_time = str(int(time.time()))
        sign = hashlib.md5(
            (self._cfg.app_id + create_time + self._cfg.app_key).encode("utf-8")
        ).hexdigest()
        try:
            resp = self._http.get(
                f"{self._cfg.base_url}/api/get_token",
                params={"appid": self._cfg.app_id, "create_time": create_time, "sign": sign},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ZhichiError(f"get_token failed: {e}") from e
        data = resp.json()
        if data.get("ret_code") != "000000":
            raise ZhichiError(f"get_token ret_code={data.get('ret_code')} {data.get('ret_msg')}")
        item = data.get("item") or {}
        token = item.get("token")
        if not token:
            raise ZhichiError("get_token no token in item")
        try:
            ttl = float(item.get("expires_in") or _TOKEN_TTL_FALLBACK)
        except (TypeError, ValueError):
            ttl = _TOKEN_TTL_FALLBACK
        # 提前 5 分钟过期
        return token, max(ttl - 300.0, 60.0)

    def _get_token(self, *, force: bool = False) -> str:
        return self._token_cache.get(self._refresh_token, force=force)

    # ---- business ----
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        def call(token: str) -> dict[str, Any]:
            resp = self._http.post(
                f"{self._cfg.base_url}{path}",
                json=payload,
                headers={"token": token, "content-type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

        token = self._get_token()
        try:
            result = call(token)
        except httpx.HTTPError as e:
            raise ZhichiError(f"POST {path} failed: {e}") from e
        if result.get("ret_code") in _TOKEN_INVALID_CODES:
            token = self._get_token(force=True)
            try:
                result = call(token)
            except httpx.HTTPError as e:
                raise ZhichiError(f"POST {path} retry failed: {e}") from e
        if result.get("ret_code") != "000000":
            raise ZhichiBusinessError(
                f"{path} ret_code={result.get('ret_code')} {result.get('ret_msg')}"
            )
        return result

    def _get(self, path: str) -> dict[str, Any]:
        def call(token: str) -> dict[str, Any]:
            resp = self._http.get(
                f"{self._cfg.base_url}{path}", headers={"token": token}
            )
            resp.raise_for_status()
            return resp.json()

        token = self._get_token()
        try:
            result = call(token)
        except httpx.HTTPError as e:
            raise ZhichiError(f"GET {path} failed: {e}") from e
        if result.get("ret_code") in _TOKEN_INVALID_CODES:
            token = self._get_token(force=True)
            result = call(token)
        if result.get("ret_code") != "000000":
            raise ZhichiBusinessError(
                f"{path} ret_code={result.get('ret_code')} {result.get('ret_msg')}"
            )
        return result

    def get_data_dict(self) -> list[dict[str, Any]]:
        """拿 agent_list（缓存 30min）。"""
        now = time.time()
        if self._agents is not None and (now - self._agents_at) < _AGENT_CACHE_TTL:
            return self._agents
        result = self._get("/api/ws/5/ticket/get_data_dict")
        item = result.get("item") or {}
        agents = item.get("agent_list") or []
        self._agents = [a for a in agents if isinstance(a, dict)]
        self._agents_at = now
        return self._agents

    def resolve_agent(self, name: str) -> str:
        """按坐席名查 agentid，查不到抛 ZhichiError。"""
        for a in self.get_data_dict():
            if a.get("agent_name") == name:
                agentid = a.get("agentid")
                if agentid:
                    return str(agentid)
        raise ZhichiError(f"坐席 {name!r} 在智齿 agent_list 中查无 agentid")

    def save_ticket_reply(self, req: ReplyTicketRequest) -> dict[str, Any]:
        from datetime import datetime

        payload: dict[str, Any] = {
            "ticketid": req.ticketid,
            "ticket_title": req.ticket_title,
            "ticket_content": req.ticket_content,
            "get_ticket_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reply_content": req.reply_content,
            "reply_type": req.reply_type,
            "reply_agentid": req.reply_agentid,
            "reply_agent_name": req.reply_agent_name,
            "ticket_status": req.ticket_status,
            "ticket_level": req.ticket_level,
        }
        if req.reply_file_str:
            payload["reply_file_str"] = req.reply_file_str
        return self._post("/api/ws/5/ticket/save_ticket_reply", payload)
```

> 校验 `adapters/_token_cache.py` 的 `TokenCache.get(refresh_fn, *, force=False)` 签名：refresh_fn 返回 `(token, ttl_seconds)`。若签名不同，按实际调整 `_get_token`。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/adapters/test_zhichi_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/adapters/zhichi/client.py backend/tests/unit/adapters/test_zhichi_client.py
git commit -m "feat(zhichi): ZhichiClient 鉴权 + save_ticket_reply + 坐席查询"
```

---

### Task 4: 入站字段映射修复（ZhichiIngester 信封解析）

**Files:**
- Modify: `backend/app/services/ingest/zhichi_ingester.py`
- Test: `backend/tests/unit/services/test_zhichi_ingester.py`

**Interfaces:**
- Consumes: 无（改现有 ingester 内部）
- Produces: ingest 后 ticket 的 title/body/product_line_code/module/reporter 字段来自真实信封；`source_payload` 存整个信封（供 Task 5 出站读 raw.deal_agent_name/ticket_level）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/services/test_zhichi_ingester.py
from app.services.ingest.zhichi_ingester import ZhichiIngester

_ENVELOPE = {
    "source": "zhichi",
    "raw": {
        "ticketid": "T20260101001",
        "ticket_title": "工单标题",
        "ticket_content": "问题描述内容",
        "ticket_level": 2,
        "user_emails": "user@example.com",
        "deal_agent_name": "莉莉",
        "enterprise_name": "某某有限公司",
        "extend_fields_list": [
            {"field_name": "产品分类", "field_type": "6", "field_text": "星空旗舰版-开票", "field_value": "opt1"},
            {"field_name": "联系手机", "field_type": "1", "field_text": "", "field_value": "13800000000"},
        ],
    },
    "fields": {
        "工单来源ID": "T20260101001",
        "主题": "工单标题",
        "问题描述": "问题描述内容",
        "产品线": "金蝶发票云",
        "产品模块": "星空旗舰版-开票",
        "联系人": "张三",
        "联系人手机": "13800000000",
        "反馈人邮箱": "user@example.com",
        "客户名称": "某某有限公司",
    },
}


def test_ingest_envelope_maps_fields(db_session):
    ing = ZhichiIngester(db_session)
    result = ing.ingest(_ENVELOPE)
    db_session.commit()
    from app.models import Ticket
    t = db_session.get(Ticket, result.ticket_id)
    assert t.source_ticket_id == "T20260101001"
    assert t.title == "工单标题"
    assert t.body == "问题描述内容"
    assert t.product_line_code == "金蝶发票云"
    assert t.module == "星空旗舰版-开票"
    assert t.reporter["name"] == "张三"
    assert t.reporter["mobile"] == "13800000000"
    assert t.reporter["email"] == "user@example.com"
    # source_payload 存整个信封，出站要用
    assert t.source_payload["raw"]["deal_agent_name"] == "莉莉"
    assert t.source_payload["raw"]["ticket_level"] == 2


def test_ingest_legacy_flat_still_works(db_session):
    # 向后兼容：无 raw/fields 的旧扁平格式
    ing = ZhichiIngester(db_session)
    result = ing.ingest({"ticketid": "OLD1", "title": "旧格式", "content": "正文", "product": "发票云"})
    db_session.commit()
    from app.models import Ticket
    t = db_session.get(Ticket, result.ticket_id)
    assert t.source_ticket_id == "OLD1"
    assert t.title == "旧格式"
    assert t.product_line_code == "发票云"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_zhichi_ingester.py -v`
Expected: FAIL（envelope 用例：product_line_code 为 None，因现有代码读顶层 product）

- [ ] **Step 3: 加信封解析层**

`backend/app/services/ingest/zhichi_ingester.py` 顶部加辅助函数 + 改 `ingest` 开头把 payload 归一化为扁平 dict：

```python
def _parse_extend_fields(raw: dict[str, Any]) -> dict[str, str]:
    """extend_fields_list → {field_name: value}。field_type=6 取 field_text，其余 field_value。"""
    out: dict[str, str] = {}
    lst = raw.get("extend_fields_list")
    if not isinstance(lst, list):
        return out
    for f in lst:
        if not isinstance(f, dict):
            continue
        name = f.get("field_name")
        if not name:
            continue
        val = f.get("field_text") if str(f.get("field_type")) == "6" else f.get("field_value")
        if val:
            out[str(name)] = str(val)
    return out


def _flatten_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """智齿真实推送 {source, raw, fields} → 归一化扁平 dict。
    fields 中文块为主源，raw + extend_fields_list 兜底。无信封则原样返回（旧扁平格式兼容）。"""
    raw = payload.get("raw")
    fields = payload.get("fields")
    if not isinstance(raw, dict) and not isinstance(fields, dict):
        return payload  # 旧扁平格式，原样
    raw = raw if isinstance(raw, dict) else {}
    fields = fields if isinstance(fields, dict) else {}
    ext = _parse_extend_fields(raw)

    def pick(*cands: Any) -> Any:
        for c in cands:
            if c:
                return c
        return None

    flat: dict[str, Any] = {
        "ticketid": pick(fields.get("工单来源ID"), raw.get("ticketid")),
        "title": pick(fields.get("主题"), raw.get("ticket_title")),
        "content": pick(fields.get("问题描述"), raw.get("ticket_content")),
        "productLineCode": pick(fields.get("产品线"), ext.get("产品分类")),
        "moduleName": pick(fields.get("产品模块"), ext.get("产品分类")),
        "customer": {
            "name": pick(fields.get("联系人"), fields.get("反馈人"), ext.get("联系人")),
            "mobile": pick(fields.get("联系人手机"), fields.get("反馈人手机"), ext.get("联系手机")),
            "email": pick(fields.get("反馈人邮箱"), raw.get("user_emails")),
            "erp_uid": pick(fields.get("对接ERP"), ext.get("对接ERP")),
        },
        "company": pick(fields.get("客户名称"), raw.get("enterprise_name")),
        # 出站要用的原始信封整体保留
        "_envelope": payload,
    }
    return flat
```

改 `ingest()` 第一行：
```python
    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        payload = _flatten_envelope(payload)
        ticketid = self._require_str(payload, "ticketid")
```

改 `source_payload` 存整个信封（若有）：
```python
            source_payload=payload.get("_envelope") or payload,
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_zhichi_ingester.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑全量智齿相关测试防回归**

Run: `cd backend && .venv/bin/pytest tests/unit -k zhichi -v`
Expected: PASS（含既有 zhichi 测试）

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ingest/zhichi_ingester.py backend/tests/unit/services/test_zhichi_ingester.py
git commit -m "feat(zhichi): 入站信封解析 — fields主源 raw兜底 + extend_fields"
```

---

### Task 5: 出站 writeback sender

**Files:**
- Create: `backend/app/services/zhichi/__init__.py`（空）
- Create: `backend/app/services/zhichi/writeback.py`
- Test: `backend/tests/unit/services/test_zhichi_writeback.py`

**Interfaces:**
- Consumes: `ZhichiClient` / `ReplyTicketRequest` / `ZhichiError`（Task 3）；`settings.zhichi_*`（Task 1）；`SyncOutbox` / `Ticket` / `HubIssue` 模型
- Produces: `drain_zhichi_outbox(db, *, client=None, settings=None) -> DrainReport`（供 Task 6）；`DrainReport`（scanned/sent/skipped/failed/deferred/errors）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/services/test_zhichi_writeback.py
from dataclasses import dataclass
import pytest
from app.models import Ticket, HubIssue, SyncOutbox, Source
from app.services.zhichi.writeback import drain_zhichi_outbox


class _FakeClient:
    def __init__(self):
        self.replies = []
    def resolve_agent(self, name):
        if name == "查无此人":
            from adapters.zhichi import ZhichiError
            raise ZhichiError("no agent")
        return "agent-" + name
    def save_ticket_reply(self, req):
        self.replies.append(req)
        return {"ret_code": "000000"}
    def close(self):
        pass


def _settings(**kw):
    @dataclass
    class S:
        zhichi_writeback_enabled: bool = True
        zhichi_writeback_dry_run: bool = False
        zhichi_writeback_batch: int = 20
        zhichi_writeback_max_attempts: int = 5
        zhichi_fallback_agent_name: str = "莉莉"
        zhichi_appid: str = "x"
        zhichi_app_key: str = "y"
        zhichi_base_url: str = "https://www.soboten.com"
    s = S()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _seed_zhichi_reply(db, *, deal_agent_name="莉莉", kind="reply"):
    db.add(Source(code="zhichi", name="智齿"))
    t = Ticket(short_code="TKT-1", source_code="zhichi", source_ticket_id="ZT1",
               type="Raw", status="received",
               source_payload={"raw": {"ticket_title": "标题", "ticket_content": "正文",
                                       "ticket_level": 2, "deal_agent_name": deal_agent_name}})
    db.add(t); db.flush()
    hub = HubIssue(short_code="HUB-1", type="Operation", title="标题", status="created",
                   reply_content="这是回复")
    db.add(hub); db.flush()
    ob = SyncOutbox(kind=kind, target_source_code="zhichi", ticket_id=t.id,
                    source_ticket_id="ZT1", hub_issue_id=hub.id,
                    payload={"reply_content": "这是回复"}, status="pending")
    db.add(ob); db.flush()
    return t, ob


def test_drain_reply_calls_save(db_session):
    _seed_zhichi_reply(db_session)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_settings())
    assert report.sent == 1
    assert fake.replies[0].ticket_status == "3"  # reply → 关单
    assert fake.replies[0].reply_agentid == "agent-莉莉"


def test_drain_dry_run_skips(db_session):
    _seed_zhichi_reply(db_session)
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_settings(zhichi_writeback_dry_run=True))
    assert report.skipped == 1
    assert report.sent == 0
    assert fake.replies == []


def test_drain_fallback_agent_when_empty(db_session):
    _seed_zhichi_reply(db_session, deal_agent_name="")
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_settings())
    assert report.sent == 1
    assert fake.replies[0].reply_agent_name == "莉莉"


def test_drain_status_in_progress_skips(db_session):
    t, ob = _seed_zhichi_reply(db_session, kind="status")
    ob.payload = {"to_status": "in_progress"}
    db_session.commit()
    fake = _FakeClient()
    report = drain_zhichi_outbox(db_session, client=fake, settings=_settings())
    assert report.skipped == 1
    assert fake.replies == []


def test_drain_disabled_returns_empty(db_session):
    _seed_zhichi_reply(db_session)
    db_session.commit()
    report = drain_zhichi_outbox(db_session, client=_FakeClient(), settings=_settings(zhichi_writeback_enabled=False))
    assert report.scanned == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_zhichi_writeback.py -v`
Expected: FAIL（ModuleNotFoundError: app.services.zhichi.writeback）

- [ ] **Step 3: 实现 writeback.py**

```python
# backend/app/services/zhichi/__init__.py
```
（空文件）

```python
# backend/app/services/zhichi/writeback.py
"""智齿出站回写 sender — drain sync_outbox（target_source_code='zhichi'）→ 智齿.

镜像 KSM writeback，但更简单：智齿一个 save_ticket_reply 搞定，无 lock→refresh→handle。
kind → ticket_status:
  reply / status released / release_note → '3'（已解决，关单）
  supply / progress_note                 → '2'（等待回复，不关单）
  status in_progress                     → skip（智齿无接管概念）
坐席：raw.deal_agent_name → resolve_agent；空则用 fallback_agent_name；查不到 → failed。
灰度：enabled(默认关) + dry_run(默认开)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.zhichi import ReplyTicketRequest, ZhichiClient, ZhichiConfig, ZhichiError
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket

logger = get_logger(__name__)

_DEFAULT_RELEASED_NOTE = "您反馈的问题已处理完成，如仍有疑问欢迎继续反馈。"


@dataclass(slots=True)
class DrainReport:
    scanned: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0
    errors: list[str] = field(default_factory=list)


def _s(v: Any) -> str:
    return "" if v is None else str(v)


class ZhichiWritebackSender:
    def __init__(self, db: Session, *, client: ZhichiClient, settings: Settings) -> None:
        self._db = db
        self._client = client
        self._settings = settings

    def drain(self) -> DrainReport:
        report = DrainReport()
        rows = list(
            self._db.execute(
                select(SyncOutbox)
                .where(
                    SyncOutbox.target_source_code == "zhichi",
                    SyncOutbox.status == "pending",
                )
                .order_by(SyncOutbox.created_at.asc())
                .limit(self._settings.zhichi_writeback_batch)
            ).scalars().all()
        )
        report.scanned = len(rows)
        for row in rows:
            self._process_row(row, report)
        return report

    def _process_row(self, row: SyncOutbox, report: DrainReport) -> None:
        ticket = self._db.get(Ticket, row.ticket_id)
        if ticket is None:
            self._mark_skipped(row, "ticket not found")
            report.skipped += 1
            return

        status_code = self._resolve_status(row)
        if status_code is None:
            self._mark_skipped(row, f"no zhichi action for kind={row.kind} payload={row.payload}")
            report.skipped += 1
            return

        if self._settings.zhichi_writeback_dry_run:
            self._mark_skipped(row, f"dry_run: would reply ticket={ticket.source_ticket_id} status={status_code}")
            report.skipped += 1
            return

        try:
            self._reply(row, ticket, status_code)
        except ZhichiError as e:
            self._record_failure(row, report, str(e))
            return
        except Exception as e:
            self._record_failure(row, report, f"unexpected: {e}")
            logger.exception("zhichi_writeback_unexpected", outbox_id=row.id)
            return

        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        row.attempts += 1
        self._db.commit()
        report.sent += 1
        logger.info("zhichi_writeback_sent", outbox_id=row.id, ticket_id=ticket.id, status=status_code)

    def _resolve_status(self, row: SyncOutbox) -> str | None:
        """kind → ticket_status，或 None（skip）。"""
        if row.kind in ("reply", "release_note"):
            return "3"
        if row.kind in ("supply", "progress_note"):
            return "2"
        if row.kind == "status":
            to = (row.payload or {}).get("to_status")
            if to == "released":
                return "3"
            # in_progress 等 → skip（智齿无接管）
        return None

    def _reply(self, row: SyncOutbox, ticket: Ticket, status_code: str) -> None:
        raw = (ticket.source_payload or {}).get("raw") or {}
        agent_name = _s(raw.get("deal_agent_name")) or self._settings.zhichi_fallback_agent_name
        agentid = self._client.resolve_agent(agent_name)  # 查不到抛 ZhichiError → failed
        req = ReplyTicketRequest(
            ticketid=_s(ticket.source_ticket_id),
            ticket_title=_s(raw.get("ticket_title") or ticket.title),
            ticket_content=_s(raw.get("ticket_content") or ticket.body),
            reply_content=self._reply_text(row),
            reply_agentid=agentid,
            reply_agent_name=agent_name,
            ticket_status=status_code,
            ticket_level=_s(raw.get("ticket_level") or "1"),
        )
        self._client.save_ticket_reply(req)

    def _reply_text(self, row: SyncOutbox) -> str:
        p = row.payload or {}
        if row.kind == "reply":
            return _s(p.get("reply_content")).strip()
        if row.kind == "supply":
            return _s(p.get("supply_note")).strip()
        if row.kind in ("release_note", "progress_note"):
            return _s(p.get("note")).strip()
        if row.kind == "status":
            hub = self._db.get(HubIssue, row.hub_issue_id)
            if hub is not None and hub.reply_content:
                return str(hub.reply_content).strip()
            return _DEFAULT_RELEASED_NOTE
        return ""

    def _mark_skipped(self, row: SyncOutbox, reason: str) -> None:
        row.status = "skipped"
        row.last_error = reason[:1000]
        self._db.commit()
        logger.info("zhichi_writeback_skipped", outbox_id=row.id, reason=reason)

    def _record_failure(self, row: SyncOutbox, report: DrainReport, error: str) -> None:
        row.attempts += 1
        row.last_error = error[:1000]
        if row.attempts >= self._settings.zhichi_writeback_max_attempts:
            row.status = "failed"
            report.failed += 1
            logger.warning("zhichi_writeback_failed", outbox_id=row.id, attempts=row.attempts, error=error)
        else:
            report.deferred += 1
            logger.info("zhichi_writeback_deferred", outbox_id=row.id, attempts=row.attempts, error=error)
        report.errors.append(f"outbox={row.id}: {error}")
        self._db.commit()


def drain_zhichi_outbox(
    db: Session,
    *,
    client: ZhichiClient | None = None,
    settings: Settings | None = None,
) -> DrainReport:
    """入口。enabled 关或凭证缺失时空转返回。"""
    settings = settings or get_settings()
    report = DrainReport()
    if not settings.zhichi_writeback_enabled:
        logger.info("zhichi_writeback_disabled")
        return report
    if not settings.zhichi_appid or not settings.zhichi_app_key:
        logger.warning("zhichi_writeback_no_credentials")
        return report

    owns_client = client is None
    if client is None:
        client = ZhichiClient(ZhichiConfig.from_settings(settings))
    try:
        return ZhichiWritebackSender(db, client=client, settings=settings).drain()
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_zhichi_writeback.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/zhichi/ backend/tests/unit/services/test_zhichi_writeback.py
git commit -m "feat(zhichi): 出站 writeback sender — kind映射+坐席兜底+灰度"
```

---

### Task 6: Celery beat 定时任务 + 主管手动端点

**Files:**
- Create: `backend/app/services/zhichi/writeback_task.py`
- Modify: `backend/app/celery_app.py`（beat_schedule 加一项）
- Modify: `backend/app/api/supervisor.py`（加 drain-zhichi 端点 + 响应模型）
- Test: `backend/tests/unit/api/test_zhichi_drain_endpoint.py`

**Interfaces:**
- Consumes: `drain_zhichi_outbox`（Task 5）；`require_supervisor`
- Produces: 端点 `POST /api/supervisor/drain-zhichi-writeback`；beat task `app.services.zhichi.writeback_task.drain_zhichi_writeback`

- [ ] **Step 1: 写失败测试（端点）**

```python
# backend/tests/unit/api/test_zhichi_drain_endpoint.py
def test_drain_zhichi_endpoint_requires_supervisor(app_client, supervisor_token):
    r = app_client.post("/api/supervisor/drain-zhichi-writeback",
                        headers={"Authorization": f"Bearer {supervisor_token}"})
    assert r.status_code == 200
    body = r.json()
    # 默认 enabled=false → 空转
    assert body["enabled"] is False
    assert body["scanned"] == 0


def test_drain_zhichi_endpoint_forbidden_for_member(app_client, member_token):
    r = app_client.post("/api/supervisor/drain-zhichi-writeback",
                        headers={"Authorization": f"Bearer {member_token}"})
    assert r.status_code == 403
```

> 校验 conftest 是否有 `supervisor_token`/`member_token` fixture；无则参照 `test_ksm_writeback` 或既有 supervisor 端点测试的 token 生成方式。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_zhichi_drain_endpoint.py -v`
Expected: FAIL（404 未注册端点）

- [ ] **Step 3: 建 writeback_task.py**

```python
# backend/app/services/zhichi/writeback_task.py
"""Celery entry: 每 2min drain 智齿 outbox。自跳过当开关关/凭证缺。"""
from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.zhichi.writeback import drain_zhichi_outbox

logger = get_logger(__name__)


@shared_task(name="app.services.zhichi.writeback_task.drain_zhichi_writeback")  # type: ignore[untyped-decorator]
def drain_zhichi_writeback() -> dict[str, int]:
    settings = get_settings()
    if not settings.zhichi_writeback_enabled:
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}
    db = make_session()
    try:
        report = drain_zhichi_outbox(db, settings=settings)
        return {"scanned": report.scanned, "sent": report.sent, "skipped": report.skipped,
                "failed": report.failed, "deferred": report.deferred}
    except Exception:
        db.rollback()
        logger.exception("zhichi_writeback_unexpected_failure")
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}
    finally:
        db.close()
```

- [ ] **Step 4: 注册 beat**

`backend/app/celery_app.py` 的 `beat_schedule` 字典里，`drain_ksm_writeback_every_2min` 后加：

```python
    "drain_zhichi_writeback_every_2min": {
        "task": "app.services.zhichi.writeback_task.drain_zhichi_writeback",
        "schedule": crontab(minute="*/2"),
    },
```

- [ ] **Step 5: 加主管端点**

`backend/app/api/supervisor.py`：
1. import：`from app.services.zhichi.writeback import drain_zhichi_outbox as drain_zhichi`
2. 响应模型（在 DrainKsmWritebackResponse 附近）：

```python
class DrainZhichiWritebackResponse(BaseModel):
    enabled: bool
    dry_run: bool
    scanned: int
    sent: int
    skipped: int
    failed: int
    deferred: int
```

3. 端点（在 drain-ksm 端点后）：

```python
@router.post("/drain-zhichi-writeback", response_model=DrainZhichiWritebackResponse)
def drain_zhichi_writeback_endpoint(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DrainZhichiWritebackResponse:
    """手动跑一轮智齿 outbox drain。尊重 zhichi_writeback_enabled/_dry_run。"""
    from app.config import get_settings

    settings = get_settings()
    report = drain_zhichi(db, settings=settings)
    logger.info(
        "supervisor_drain_zhichi_writeback",
        operator_user_id=user.user_id,
        scanned=report.scanned,
        sent=report.sent,
        failed=report.failed,
    )
    return DrainZhichiWritebackResponse(
        enabled=settings.zhichi_writeback_enabled,
        dry_run=settings.zhichi_writeback_dry_run,
        scanned=report.scanned,
        sent=report.sent,
        skipped=report.skipped,
        failed=report.failed,
        deferred=report.deferred,
    )
```

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_zhichi_drain_endpoint.py -v`
Expected: PASS

- [ ] **Step 7: 重新生成 OpenAPI 类型（新端点）**

Run: `cd backend && make openapi-dump 2>&1 | tail -3`（若目标存在）
说明：新增端点会改 openapi.json，CI `make check-types` 会校验。生成后一并提交。

- [ ] **Step 8: 提交**

```bash
git add backend/app/services/zhichi/writeback_task.py backend/app/celery_app.py backend/app/api/supervisor.py backend/tests/unit/api/test_zhichi_drain_endpoint.py backend/frontend 2>/dev/null; git add frontend/src/api 2>/dev/null
git commit -m "feat(zhichi): beat 定时 drain + 主管手动端点"
```

---

### Task 7: 全量验证 + lint + 类型检查

**Files:** 无新增，回归验证

- [ ] **Step 1: 全量单测**

Run: `cd backend && .venv/bin/pytest -m "not integration and not e2e and not eval" -q 2>&1 | tail -20`
Expected: 全绿，覆盖率 ≥70%

- [ ] **Step 2: lint + 类型**

Run: `cd backend && make lint 2>&1 | tail -20`
Expected: ruff + mypy 通过

- [ ] **Step 3: 修复任何 lint/type 报错后重跑，全绿**

- [ ] **Step 4: 提交（若有 lint 修复）**

```bash
git add -A && git commit -m "chore(zhichi): lint + type 修复" || echo "无改动"
```

---

### Task 8: 部署 SIT

**Files:** 无代码改动，部署操作

- [ ] **Step 1: 合并到 main（或按团队流程 PR）**

```bash
git checkout main && git merge --no-ff feat/zhichi-integration
git push origin main
```

- [ ] **Step 2: SIT 拉代码 + 重建镜像**

```bash
ssh root@sit "cd /data/hub-issue && git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build"
```

- [ ] **Step 3: 无迁移（本次无 DB 变更），确认健康**

```bash
ssh root@sit "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health"
```
Expected: 200

- [ ] **Step 4: SIT 验证入站（造真实信封工单）**

用 webhook token POST 一条 `{source,raw,fields}` 智齿信封到 `/webhook/zhichi`，确认 product_line_code/module/客户字段正确解析（对比修复前全丢）。

- [ ] **Step 5: SIT 验证出站（dry_run 观察）**

`deploy/.env` 配 `ZHICHI_APPID`/`ZHICHI_APP_KEY` + `ZHICHI_WRITEBACK_ENABLED=true` + `ZHICHI_WRITEBACK_DRY_RUN=true`，重启后对一条智齿 Operation 工单回复，触发 `POST /api/supervisor/drain-zhichi-writeback`，看 log 里组装的 payload 正确。

- [ ] **Step 6: 真打验证（可选，需你确认）**

翻 `ZHICHI_WRITEBACK_DRY_RUN=false`，再 drain 一次，确认回复真到智齿工单。**这步会真实写智齿，需你确认后再做。**

---

## Self-Review

**Spec 覆盖：** §3 入站→Task 4；§4 adapter→Task 2/3；§5 writeback→Task 5；§6 config/beat/端点→Task 1/6；§7 测试→各 Task 内嵌 + Task 7；§8 灰度上线→Task 8。全覆盖。

**占位扫描：** 无 TODO/TBD，每步含完整代码/命令。

**类型一致性：** `drain_zhichi_outbox`(Task5) 签名与 Task6 调用一致；`ReplyTicketRequest` 字段 Task2 定义、Task3/5 使用一致；`DrainReport` 字段与 KSM 对齐；`resolve_agent`/`save_ticket_reply` Task3 定义、Task5 使用一致。

**已知需实现时校验的点：**
1. `adapters/_token_cache.py` 的 `TokenCache.get(fn, *, force)` 真实签名（Task 3 Step 3 已标注校验）
2. conftest 的 `supervisor_token`/`member_token`/`db_session` fixture 是否存在（Task 6 已标注）
3. `make openapi-dump` 目标名（Task 6 Step 7）
