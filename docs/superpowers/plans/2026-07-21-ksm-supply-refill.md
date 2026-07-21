# KSM 补料回流处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客户补料后 KSM 重推同 billId 时识别为补料回填，更新工单内容并复用异步 drain 自动重答，而非当前的纯 no-op 丢弃。

**Architecture:** ticket 层新增 `awaiting_supply` 状态（补料请求真发成功后才置）作为门控；KSM ingester 命中已存在 ticket 且状态为 `awaiting_supply` 时调公共服务 `apply_supply_refill` 更新内容 + 复位状态 + 清 hub 的 auto_reply 审计；既有 `drain_operation_auto_reply`（每 2min）因审计被清而自然重扫重答。零新定时任务、零重分类。

**Tech Stack:** Python 3.11 / SQLAlchemy / pytest（SQLite in-memory）。后端目录 `backend/`，命令在 `backend/` 下用 `.venv/bin/...`。

## Global Constraints

- 范围**仅 KSM**；公共服务 `supply_refill.py` 设计成跨源可复用但本期只接 KSM ingester。
- `awaiting_supply` 只在 KSM writeback sender **真发 supplyKsmOrder 成功后**置，dry_run / 失败均不置。
- 补料回填分支返回 `deduped=True`（不走 post-ingest，不重跑 triage/分类）；重答靠清 auto_reply 审计后由 drain 接管。
- Ticket.status 是自由字符串无 CHECK 约束，`awaiting_supply` **无需迁移**。
- `AgentDecision` 无 `deleted_at`，清审计 = 硬 `db.delete()`。
- 北京时区用 `app/services/sla/workday.py` 的 `BEIJING = timezone(timedelta(hours=8))`。
- `StatusHistoryRepository.record` 签名：`record(*, entity_type, entity_id, from_status, to_status, changed_by, reason=None, metadata=None)`。
- lint/type 门槛：`.venv/bin/ruff check` + `.venv/bin/ruff format --check` + `.venv/bin/mypy` 必须过。

---

### Task 1: 补料回填公共服务 `apply_supply_refill`

**Files:**
- Create: `backend/app/services/ingest/supply_refill.py`
- Test: `backend/tests/unit/services/test_supply_refill.py`

**Interfaces:**
- Consumes: `Ticket`, `HubIssue`, `AgentDecision` from `app.models`；`StatusHistoryRepository`；`BEIJING` from `app.services.sla.workday`。
- Produces: `apply_supply_refill(db: Session, ticket: Ticket, new_payload: dict[str, Any]) -> bool` —— 纯机械物化，不 commit（调用方负责）。前置条件由调用方保证 `ticket.status == "awaiting_supply"`。返回 `True`（已回填）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/services/test_supply_refill.py`：

```python
"""补料回填公共服务单测。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AgentDecision, HubIssue, Ticket
from app.services.ingest.supply_refill import apply_supply_refill


def _seed(db: Session, *, reply_v: int = 0, with_hub: bool = True) -> tuple[Ticket, HubIssue | None]:
    hub = None
    if with_hub:
        hub = HubIssue(
            short_code="HUB-RF-1",
            type="Operation",
            title="开票失败",
            canonical_body="旧内容",
            status="created",
            reply_content_version=reply_v,
        )
        db.add(hub)
        db.flush()
    t = Ticket(
        short_code="TKT-RF-1",
        source_code="ksm",
        source_ticket_id="bill-1",
        type="Raw",
        status="awaiting_supply",
        source_payload={"billId": "bill-1", "content": "旧内容"},
        title="开票失败",
        body="开票时提示网络错误",
        hub_issue_id=hub.id if hub else None,
    )
    db.add(t)
    db.flush()
    if hub is not None:
        db.add(
            AgentDecision(
                decision_type="auto_reply",
                subject_type="hub_issue",
                subject_id=hub.id,
                proposal={"branch": "transfer"},
            )
        )
        db.flush()
    return t, hub


def test_refill_updates_content_resets_status_clears_audit(db_session: Session) -> None:
    t, hub = _seed(db_session)
    db_session.commit()
    new_payload = {"billId": "bill-1", "content": "客户补充了报错截图和复现步骤"}
    ok = apply_supply_refill(db_session, t, new_payload)
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert t.source_payload == new_payload
    assert "客户补充了报错截图和复现步骤" in (t.body or "")
    assert "[补料回填" in (t.body or "")
    assert t.status == "received"
    # auto_reply 审计被清 → drain 会重扫
    remaining = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .count()
    )
    assert remaining == 0


def test_refill_preserves_audit_when_already_replied(db_session: Session) -> None:
    """hub 已答复(reply_v>=1)是矛盾状态：只更新内容，不清审计、不复位为可重答。"""
    t, hub = _seed(db_session, reply_v=1)
    db_session.commit()
    ok = apply_supply_refill(db_session, t, {"billId": "bill-1", "content": "新内容"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert "新内容" in (t.body or "")
    # 已答复 → 审计保留（不覆盖已发答复）
    remaining = (
        db_session.query(AgentDecision)
        .filter_by(decision_type="auto_reply", subject_id=hub.id)
        .count()
    )
    assert remaining == 1


def test_refill_no_hub_only_updates_content(db_session: Session) -> None:
    t, _ = _seed(db_session, with_hub=False)
    db_session.commit()
    ok = apply_supply_refill(db_session, t, {"billId": "bill-1", "content": "无hub新内容"})
    db_session.commit()
    assert ok is True
    db_session.refresh(t)
    assert "无hub新内容" in (t.body or "")
    assert t.status == "received"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_supply_refill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ingest.supply_refill'`

- [ ] **Step 3: 写实现**

创建 `backend/app/services/ingest/supply_refill.py`：

```python
"""补料回填公共服务（跨源可复用；本期只接 KSM ingester）.

客户补料后源系统重推同一张单，ingester 命中已存在 ticket 且其状态为
`awaiting_supply` 时调本服务：把新补料内容物化进 ticket，复位状态，并清掉
关联 hub 的 auto_reply 审计——好让既有 drain_operation_auto_reply 重扫重答。

纯机械物化，无 LLM。不 commit（调用方负责事务边界）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.sla.workday import BEIJING

logger = get_logger(__name__)

_CHANGED_BY = "system:supply_refill"


def apply_supply_refill(db: Session, ticket: Ticket, new_payload: dict[str, Any]) -> bool:
    """把补料回推的新内容物化进 ticket。前置：ticket.status == "awaiting_supply"（调用方保证）。

    步骤：
      1. source_payload 覆盖为 new_payload（含新补料内容/附件/节点）
      2. body 追加 [补料回填 北京时间] 段（新 content）
      3. status 复位 awaiting_supply → received，写 status_history
      4. 关联 hub：不存在/已删 → 只更新内容；reply_v>=1（已答复矛盾态）→ 只更新内容 +
         记 status_history 留主管；否则清该 hub 的 auto_reply 审计（drain 重扫）
    返回 True。
    """
    history = StatusHistoryRepository(db)
    new_content = str(new_payload.get("content") or "").strip()

    ticket.source_payload = new_payload
    if new_content:
        stamp = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
        prev_body = ticket.body or ""
        ticket.body = f"{prev_body}\n\n[补料回填 {stamp}]\n{new_content}".strip()

    prev_status = ticket.status
    ticket.status = "received"
    history.record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=prev_status,
        to_status="received",
        changed_by=_CHANGED_BY,
        reason="补料回填：客户补充资料，工单复位待重新处理",
    )

    hub = db.get(HubIssue, ticket.hub_issue_id) if ticket.hub_issue_id else None
    if hub is None or hub.deleted_at is not None:
        logger.info("supply_refill_no_hub", ticket_id=ticket.id)
        return True

    if hub.reply_content_version >= 1:
        # 已答复矛盾态：不清审计（不覆盖已发答复），记审计留主管
        history.record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=hub.status,
            to_status=hub.status,
            changed_by=_CHANGED_BY,
            reason="补料回填但 hub 已答复（reply_v>=1），留主管人工判断",
        )
        logger.info("supply_refill_already_replied", ticket_id=ticket.id, hub_issue_id=hub.id)
        return True

    cleared = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.decision_type == "auto_reply",
            AgentDecision.subject_type == "hub_issue",
            AgentDecision.subject_id == hub.id,
        )
        .all()
    )
    for d in cleared:
        db.delete(d)
    logger.info(
        "supply_refill_cleared_audit",
        ticket_id=ticket.id,
        hub_issue_id=hub.id,
        cleared=len(cleared),
    )
    return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_supply_refill.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: lint + type**

Run: `.venv/bin/ruff check app/services/ingest/supply_refill.py tests/unit/services/test_supply_refill.py && .venv/bin/ruff format app/services/ingest/supply_refill.py tests/unit/services/test_supply_refill.py && .venv/bin/mypy app/services/ingest/supply_refill.py`
Expected: All checks passed / Success

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ingest/supply_refill.py backend/tests/unit/services/test_supply_refill.py
git commit -m "feat(supply-refill): 补料回填公共服务（更新内容+复位+清审计）"
```

---

### Task 2: KSM ingester 补料分支

**Files:**
- Modify: `backend/app/services/ingest/ksm_ingester.py`（幂等分支 `ingest()` 内 `existing is not None` 段，约第 72-90 行；顶部 import）
- Test: `backend/tests/unit/services/test_ksm_ingester.py`

**Interfaces:**
- Consumes: `apply_supply_refill` from Task 1；`IngestResult`（已有）。
- Produces: 无新公共接口；行为变更——`existing.status == "awaiting_supply"` 时先调 `apply_supply_refill(self._db, existing, payload)` 再返回 `deduped=True`；其他状态维持原 no-op。抽 `_dedup_result(existing, *, deduped) -> IngestResult` 私有助手（供两分支复用，字段取值与原第 80-90 行完全一致）。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/unit/services/test_ksm_ingester.py` 末尾追加。先看文件头已有的 fixture / 构造 helper 复用（用现有 payload 构造方式；下面用最小独立 seed 以免耦合）：

```python
def test_ingest_supply_refill_when_awaiting(db_session, monkeypatch) -> None:
    """已存在 ticket 且 awaiting_supply → 调 apply_supply_refill，返回 deduped=True。"""
    from app.models import Source, Ticket
    from app.services.ingest import ksm_ingester as mod

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    existing = Ticket(
        short_code="TKT-SR-1",
        source_code="ksm",
        source_ticket_id="bill-refill-1",
        type="Raw",
        status="awaiting_supply",
        source_payload={"billId": "bill-refill-1"},
        title="t",
        body="b",
    )
    db_session.add(existing)
    db_session.commit()

    called: dict = {}

    def fake_refill(db, ticket, payload):
        called["ticket_id"] = ticket.id
        called["payload"] = payload
        return True

    monkeypatch.setattr(mod, "apply_supply_refill", fake_refill)
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-refill-1", "content": "新补料"})
    assert called["ticket_id"] == existing.id
    assert called["payload"]["content"] == "新补料"
    assert result.deduped is True
    assert result.ticket_id == existing.id


def test_ingest_dedup_noop_when_not_awaiting(db_session, monkeypatch) -> None:
    """已存在 ticket 但非 awaiting_supply（重复心跳）→ 原 no-op，不调 refill。"""
    from app.models import Source, Ticket
    from app.services.ingest import ksm_ingester as mod

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    existing = Ticket(
        short_code="TKT-SR-2",
        source_code="ksm",
        source_ticket_id="bill-refill-2",
        type="Raw",
        status="received",
        source_payload={"billId": "bill-refill-2"},
        title="t",
        body="b",
    )
    db_session.add(existing)
    db_session.commit()

    called = {"n": 0}
    monkeypatch.setattr(mod, "apply_supply_refill", lambda *a, **k: called.__setitem__("n", 1))
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-refill-2", "content": "重复心跳"})
    assert called["n"] == 0
    assert result.deduped is True
    assert result.ticket_id == existing.id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_ingester.py -v -k "supply_refill or dedup_noop"`
Expected: FAIL — `AttributeError: module ... has no attribute 'apply_supply_refill'`（monkeypatch 目标不存在）

- [ ] **Step 3: 写实现**

在 `ksm_ingester.py` 顶部 import 段（第 31 行 `upsert_catalog` 后）加：

```python
from app.services.ingest.supply_refill import apply_supply_refill
```

把 `ingest()` 里现有的幂等分支（第 72-90 行）：

```python
        # 1. Idempotency: skip if already ingested
        existing = self._tickets.find_by_source("ksm", bill_id)
        if existing is not None:
            logger.info(
                "ksm_ingest_dedup",
                bill_id=bill_id,
                existing_ticket_id=existing.id,
            )
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                customer_id=(existing.customer_identity_id and self._customer_id_of(existing)) or 0,
                customer_identity_id=existing.customer_identity_id or 0,
                routing_decision="dedup",
                assigned_user_ids=(
                    [existing.assigned_user_id] if existing.assigned_user_id else []
                ),
                deduped=True,
            )
```

替换为：

```python
        # 1. Idempotency: skip if already ingested
        existing = self._tickets.find_by_source("ksm", bill_id)
        if existing is not None:
            if existing.status == "awaiting_supply":
                # 补料回填：客户补料后 KSM 重推同 billId。更新内容 + 复位 + 清审计，
                # 让 drain_operation_auto_reply 重扫重答。仍 deduped=True（不走 post-ingest，
                # 不重跑 triage/分类）——重答靠清审计后 drain 接管。
                apply_supply_refill(self._db, existing, payload)
                logger.info(
                    "ksm_ingest_supply_refill", bill_id=bill_id, existing_ticket_id=existing.id
                )
                return self._dedup_result(existing)
            logger.info("ksm_ingest_dedup", bill_id=bill_id, existing_ticket_id=existing.id)
            return self._dedup_result(existing)
```

在类内加私有助手（放 `_customer_id_of` 附近，约第 205 行前）：

```python
    def _dedup_result(self, existing: Ticket) -> IngestResult:
        """已存在 ticket 的返回结果（重复心跳与补料回填共用；字段全取自 existing）。"""
        return IngestResult(
            ticket_id=existing.id,
            short_code=existing.short_code,
            customer_id=(existing.customer_identity_id and self._customer_id_of(existing)) or 0,
            customer_identity_id=existing.customer_identity_id or 0,
            routing_decision="dedup",
            assigned_user_ids=[existing.assigned_user_id] if existing.assigned_user_id else [],
            deduped=True,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_ingester.py -v`
Expected: PASS（含既有用例 + 2 个新用例全绿）

- [ ] **Step 5: lint + type**

Run: `.venv/bin/ruff check app/services/ingest/ksm_ingester.py tests/unit/services/test_ksm_ingester.py && .venv/bin/ruff format app/services/ingest/ksm_ingester.py tests/unit/services/test_ksm_ingester.py && .venv/bin/mypy app/services/ingest/ksm_ingester.py`
Expected: All checks passed / Success

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ingest/ksm_ingester.py backend/tests/unit/services/test_ksm_ingester.py
git commit -m "feat(ksm-ingest): 补料回填分支（awaiting_supply → apply_supply_refill）"
```

---

### Task 3: KSM writeback 真发成功后置 awaiting_supply

**Files:**
- Modify: `backend/app/services/ksm/writeback.py`（`_send_one`/drain 成功落点约第 235-244 行；新增 `_mark_awaiting_supply` 方法）
- Test: `backend/tests/unit/services/test_ksm_writeback.py`

**Interfaces:**
- Consumes: 现有 `SyncOutbox` / `Ticket` / `StatusHistoryRepository` / `_TICKET_TERMINAL_STATUSES`（`writeback.py:67`）。
- Produces: 行为变更——action 为 `supply` 且真发成功后，`ticket.status` 非终态则置 `awaiting_supply` + 写 status_history。dry_run（走 skipped 分支不进此路径）和失败（`_record_failure` 后 return）均不置。

- [ ] **Step 1: 写失败测试**

先确认 `test_ksm_writeback.py` 里既有的构造方式（client stub / settings stub / outbox 构造）。追加（按文件既有 helper 命名适配；下面给出行为断言，构造复用文件顶部现有 fixtures）：

```python
def test_supply_send_marks_awaiting_supply(db_session, ksm_writeback_env) -> None:
    """supply 真发成功 → ticket 置 awaiting_supply + status_history。

    ksm_writeback_env: 复用本文件既有的「enabled + dry_run=False + handler 已配 +
    stub client 成功」环境 fixture；若文件用别的方式搭环境，照既有 supply 成功用例改造。
    """
    # 构造一条 supply outbox + 关联 ticket（status=received），跑 drain，
    # 断言 ticket.status == "awaiting_supply" 且有对应 status_history 行。
    ...


def test_supply_dry_run_does_not_mark_awaiting(db_session, ksm_writeback_dry_run_env) -> None:
    """dry_run → 只 skipped，不置 awaiting_supply（ticket 状态不变）。"""
    ...


def test_supply_send_failure_does_not_mark_awaiting(db_session, ksm_writeback_fail_env) -> None:
    """supply 发送失败（KSMError）→ 标 failed/重试，不置 awaiting_supply。"""
    ...
```

> **实现者注**：`test_ksm_writeback.py` 已有 supply 成功/ dry_run / 失败的既有用例（`_supply` 路径）。照最接近的既有用例复制其构造脚手架，只把断言改成检查 `ticket.status`。不要新造 fixture 名——用文件里已存在的构造方式。三条断言分别对应：成功→`awaiting_supply`、dry_run→状态不变、失败→状态不变。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_writeback.py -v -k "awaiting"`
Expected: FAIL（成功用例断言 `awaiting_supply` 不成立，当前 supply 不改状态）

- [ ] **Step 3: 写实现**

在 `writeback.py` drain 成功落点（第 240-241 行）：

```python
        if action in _CLOSING_ACTIONS:
            self._close_local(row, ticket)
```

改为：

```python
        if action in _CLOSING_ACTIONS:
            self._close_local(row, ticket)
        elif action == "supply":
            self._mark_awaiting_supply(row, ticket)
```

新增方法（放 `_close_local` 后，约第 274 行后）：

```python
    def _mark_awaiting_supply(self, row: SyncOutbox, ticket: Ticket) -> None:
        """补料请求真发成功后：ticket → awaiting_supply（挂起等客户回填）。

        含义是「补料请求已送达客户」——dry_run（skipped，不进此路径）和失败
        （_record_failure 后 return，不到这里）都不会置此状态。终态不动（幂等）。
        不 commit（随外层）。
        """
        if ticket.status in _TICKET_TERMINAL_STATUSES:
            return
        prev = ticket.status
        ticket.status = "awaiting_supply"
        StatusHistoryRepository(self._db).record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=prev,
            to_status="awaiting_supply",
            changed_by="system:ksm_writeback",
            reason=f"补料请求回写成功，挂起等客户回填（outbox={row.id}）",
        )
```

> 确认 `writeback.py` 顶部已 import `StatusHistoryRepository`（`_close_local` 已用，故已在）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_writeback.py -v`
Expected: PASS（既有用例 + 3 个新断言全绿）

- [ ] **Step 5: lint + type**

Run: `.venv/bin/ruff check app/services/ksm/writeback.py tests/unit/services/test_ksm_writeback.py && .venv/bin/ruff format app/services/ksm/writeback.py tests/unit/services/test_ksm_writeback.py && .venv/bin/mypy app/services/ksm/writeback.py`
Expected: All checks passed / Success

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ksm/writeback.py backend/tests/unit/services/test_ksm_writeback.py
git commit -m "feat(ksm-writeback): 补料真发成功后置 ticket awaiting_supply"
```

---

### Task 4: 端到端回归 + 全套验证

**Files:**
- 无新增；跑全套确认无回归。

- [ ] **Step 1: 全套 lint + type**

Run（在 `backend/`）：`.venv/bin/ruff check app/ tests/ && .venv/bin/ruff format --check app/ && .venv/bin/mypy app/`
Expected: All checks passed / Success（若 tests/ 有 pre-existing ruff error 如 zhichi 用例，只需确认不是本次改动引入的）

- [ ] **Step 2: 全套 unit（排除已知 pre-existing GLM 网络测试）**

Run: `.venv/bin/pytest -q --deselect tests/unit/adapters/test_glm_client.py::test_network_error`
Expected: all passed（仅剔除已知的真实网络 502 测试）

- [ ] **Step 3: 手工串一遍数据流验证（无自动化 e2e，逐点确认）**

在测试或 REPL 中确认这条链闭合：
1. `request_supply(db, hub_id, note=..., requested_by=...)` → supply outbox 入队，ticket 状态不变。
2. KSM writeback drain 真发成功 → ticket → `awaiting_supply`。
3. 同 billId 再 `KSMIngester.ingest(payload_带新content)` → `apply_supply_refill` 触发 → ticket.source_payload 更新、body 追加、status→received、hub 的 auto_reply 审计被清、`deduped=True`。
4. `drain_operation_auto_reply(db, settings=开启)` → 该 hub 重新入选（reply_v=0 且无 auto_reply 审计）→ 走重答。

可写一个临时集成测试覆盖 1→4（可选，若时间允许放 `tests/unit/services/test_supply_refill.py` 作 `test_end_to_end_refill_reanswer`），跑完删除临时脚手架。

- [ ] **Step 4: 无临时文件残留**

Run: `git status --short`
Expected: 只有计划内的改动，无临时脚本/输出文件。

- [ ] **Step 5: 更新记忆**

在 `~/.claude/.../memory/` 新建或更新一条 project 记忆，记录 KSM 补料回流已实现（awaiting_supply 门控 + apply_supply_refill + 复用 drain 重答），并在 MEMORY.md 加指针。链接 [[operation-auto-reply-async]]。

---

## Self-Review

**1. Spec coverage：**
- 补料识别（awaiting_supply 门控）→ Task 2 + Task 3 ✓
- 内容更新（source_payload/body）→ Task 1 ✓
- 状态复位 received → Task 1 ✓
- 清 auto_reply 审计驱动 drain → Task 1 ✓
- awaiting_supply 真发成功后才置 → Task 3（含 dry_run/失败不置的测试）✓
- deduped=True 不走 post-ingest → Task 2 ✓
- 边界：reply_v≥1 保护 / hub 已删只更新 → Task 1 测试 ✓；ai_cs 排除（drain 既有逻辑，无需改）✓；主管改状态后回推 no-op（非 awaiting_supply 分支）→ Task 2 ✓
- 无迁移 → Global Constraints 已声明，无 migration 任务 ✓
- 仅 KSM，公共服务预留智齿 → Task 1 独立文件 ✓

**2. Placeholder scan：** Task 3 测试用 `...` 占位，但明确标注「照文件既有 supply 成功/dry_run/失败用例复制脚手架，只改断言」——因 test_ksm_writeback.py 的 fixture 命名需实现者现场对齐，不硬编造可能不存在的 fixture 名。其余步骤均有完整代码。

**3. Type consistency：** `apply_supply_refill(db, ticket, new_payload) -> bool` 在 Task 1 定义、Task 2 调用一致；`_dedup_result(existing) -> IngestResult` Task 2 内自洽；`_mark_awaiting_supply(row, ticket)` Task 3 内自洽。`StatusHistoryRepository.record` 关键字参数与 Global Constraints 一致。
