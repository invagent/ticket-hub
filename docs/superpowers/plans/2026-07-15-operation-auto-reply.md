# Operation 自动答复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operation 工单毕业 hub_issue 后，自动调 ai_cs agent（replay）生成答复，harness 硬判可发则走 author_reply 级联回写客户，否则留主管。

**Architecture:** 新建 `services/agents/operation_answer.py` 的 `auto_answer_operation(hub_issue_id)`：拼问题 → `ai_cs.replay` → 硬判 → `author_reply`（复用 cascade→outbox→KSM/智齿回写）。接入 `webhooks.py` `_route_by_type` 的 Operation 分支。灰度开关默认关。

**Tech Stack:** Python 3.11/3.12 / FastAPI / SQLAlchemy / pytest。复用 `adapters/ai_cs`（replay）+ `services/cascade/reply_sync`（author_reply）+ `services/knowledge_feedback/service.build_client`。

## Global Constraints

- Python `>=3.11,<3.14`。
- ai_cs 来源（`source_code=="ai_cs"`）的 escalation 工单**不自动答复**（走 reflect 反思队列）。
- 答复走 `author_reply(db, hub_issue_id, content=, authored_by="agent:ai_cs")`——复用现有 cascade+outbox，不新写回写。
- 出站真发受 `ksm/zhichi_writeback_enabled + dry_run` 二层灰度保护（本功能只管"生成答复入 outbox"）。
- 审计 decision_type=`auto_reply`（CHECK 已有）；authored_by=`agent:ai_cs`。
- 任何不确定都留主管（hub_issue 停 created），绝不静默发。
- 分支 `feat/operation-auto-reply`（已建，spec 已提交）。每 Task 末尾 commit。

---

### Task 1: config 新增自动答复开关

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `settings.operation_auto_reply_enabled`(bool, F) / `operation_auto_reply_min_length`(int, 10)

- [ ] **Step 1: 写失败测试**

```python
# 追加到 backend/tests/unit/test_config.py
def test_operation_auto_reply_defaults() -> None:
    s = get_settings()
    assert s.operation_auto_reply_enabled is False
    assert s.operation_auto_reply_min_length == 10
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py::test_operation_auto_reply_defaults -v`
Expected: FAIL（AttributeError）

- [ ] **Step 3: 加配置**

`config.py` 在 Linear/hub 段附近加：
```python
    # ---- Operation 自动答复（调 ai_cs replay 生成答复回写客户）----
    # 默认关：开了才对新毕业的 Operation hub_issue 自动答复。出站真发仍受
    # ksm/zhichi_writeback_enabled + dry_run 二层灰度保护（双保险）。
    operation_auto_reply_enabled: bool = False
    operation_auto_reply_min_length: int = 10  # 答复短于此视为无效，留主管
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py
git commit -m "feat(auto-reply): config 开关 operation_auto_reply_enabled + min_length"
```

---

### Task 2: operation_answer 服务 — 核心逻辑

**Files:**
- Create: `backend/app/services/agents/operation_answer.py`
- Test: `backend/tests/unit/services/test_operation_answer.py`

**Interfaces:**
- Consumes: `build_client`(knowledge_feedback.service) / `AiCsError`(adapters.ai_cs) / `author_reply`(cascade.reply_sync) / `HubIssue`/`Ticket`/`AgentDecision` 模型
- Produces: `auto_answer_operation(db, hub_issue_id, *, settings=None) -> bool`（True=已答复，False=留主管）；`_is_answer_sendable(answer, min_length) -> bool`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/services/test_operation_answer.py
from dataclasses import dataclass
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, Ticket, AgentDecision
from app.services.agents.operation_answer import auto_answer_operation, _is_answer_sendable
from adapters.ai_cs import AiCsError
from adapters.ai_cs.types import ReplayResult


@dataclass
class _S:
    operation_auto_reply_enabled: bool = True
    operation_auto_reply_min_length: int = 10
    knowledge_feedback_enabled: bool = True
    ai_cs_app_id: str = "x"
    ai_cs_app_key: str = "y"
    ai_cs_base_url: str = "http://localhost:9090"
    ai_cs_managed_skills: str = "customer-service"


class _FakeClient:
    def __init__(self, answer="", raise_err=False):
        self._answer = answer
        self._raise = raise_err
    def replay(self, **kw):
        if self._raise:
            raise AiCsError("boom")
        return ReplayResult(answer=self._answer, cited_knowledge=[], skills_used=[], trace_id="t1")
    def close(self):
        pass


def _seed_op_hub(db: Session):
    if db.query(Source).filter_by(code="ksm").first() is None:
        db.add(Source(code="ksm", name="KSM"))
    hub = HubIssue(short_code="HUB-OP1", type="Operation", title="开票失败",
                   canonical_body="开票时提示网络错误", status="created", product_line_code=None,
                   product="发票云", module="开票")
    db.add(hub); db.flush()
    t = Ticket(short_code="TKT-OP1", source_code="ksm", source_ticket_id="K1",
               type="Raw", status="received", hub_issue_id=hub.id, title="开票失败", body="开票时提示网络错误")
    db.add(t); db.flush()
    return hub, t


def test_sendable_rules():
    assert _is_answer_sendable("请在设置页重新绑定后重试即可解决。", 10) is True
    assert _is_answer_sendable("", 10) is False
    assert _is_answer_sendable("好的", 10) is False  # 太短
    assert _is_answer_sendable("抱歉，此问题需转人工客服处理。", 10) is False  # 转人工信号


def test_auto_answer_sends(db_session: Session):
    hub, t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票，若仍失败请提供截图。")
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 1
    assert hub.reply_authored_by == "agent:ai_cs"
    # 审计
    d = db_session.query(AgentDecision).filter_by(decision_type="auto_reply", subject_id=hub.id).first()
    assert d is not None


def test_auto_answer_empty_leaves_to_human(db_session: Session):
    hub, t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(answer="")
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.reply_content_version == 0  # 未答复


def test_auto_answer_replay_error_leaves_to_human(db_session: Session):
    hub, t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(raise_err=True)
    with patch("app.services.agents.operation_answer.build_client", return_value=fake):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False


def test_auto_answer_disabled(db_session: Session):
    hub, t = _seed_op_hub(db_session); db_session.commit()
    ok = auto_answer_operation(db_session, hub.id, settings=_S(operation_auto_reply_enabled=False))
    assert ok is False


def test_auto_answer_ai_cs_source_skipped(db_session: Session):
    # escalation(ai_cs) 来源不自动答复
    if db_session.query(Source).filter_by(code="ai_cs").first() is None:
        db_session.add(Source(code="ai_cs", name="AI客服"))
    hub = HubIssue(short_code="HUB-OP2", type="Operation", title="x", status="created")
    db_session.add(hub); db_session.flush()
    t = Ticket(short_code="TKT-OP2", source_code="ai_cs", source_ticket_id="A1",
               type="Raw", status="received", hub_issue_id=hub.id, title="x", body="x")
    db_session.add(t); db_session.commit()
    ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 operation_answer.py**

```python
# backend/app/services/agents/operation_answer.py
"""Operation 自动答复（ADR-0016 §3「Operation 未命中 → 直接答复客户」）.

Operation hub_issue 毕业后，调 ai_cs agent（replay）生成答复，harness 硬判可发
则走 author_reply 级联回写客户（复用 cascade→outbox→KSM/智齿回写关单），否则
留主管。triage 已分类故不重走 A/B/C/D。escalation(ai_cs) 来源不走此路（走 reflect）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from adapters.ai_cs import AiCsError
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import AgentDecision, HubIssue, Ticket
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.knowledge_feedback.service import (
    KnowledgeFeedbackDisabledError,
    build_client,
)

logger = get_logger(__name__)

_TRANSFER_HINTS = ("转人工", "无法回答", "无法处理", "请联系", "人工客服")


def _is_answer_sendable(answer: str, min_length: int) -> bool:
    """harness 硬判：答复能否直接发给客户。"""
    a = (answer or "").strip()
    if len(a) < min_length:
        return False
    return not any(h in a for h in _TRANSFER_HINTS)


def auto_answer_operation(
    db: Session, hub_issue_id: int, *, settings: Settings | None = None
) -> bool:
    """对新毕业的 Operation hub_issue 自动答复。True=已答复，False=留主管。
    自吞异常（不阻塞入库链）。"""
    settings = settings or get_settings()
    if not settings.operation_auto_reply_enabled:
        return False

    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None or hub.type != "Operation":
        return False

    # escalation(ai_cs) 来源不自动答复（走 reflect 反思队列）
    linked = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .first()
    )
    if linked is not None and linked.source_code == "ai_cs":
        return False

    try:
        client = build_client(settings)
    except KnowledgeFeedbackDisabledError:
        logger.info("operation_auto_reply_ai_cs_disabled", hub_issue_id=hub.id)
        return False

    product = hub.product or hub.product_line_code or ""
    module = hub.module or ""
    body = hub.canonical_body or hub.title or ""
    question = f"{product}-{module}：{body}" if module else f"{product}：{body}"
    question = question.lstrip("-：").strip() or body

    try:
        result = client.replay(question=question, use_latest_knowledge=True)
        answer = result.answer
        trace_id = result.trace_id
    except AiCsError as e:
        logger.warning("operation_auto_reply_replay_failed", hub_issue_id=hub.id, error=str(e))
        return False
    finally:
        client.close()

    if not _is_answer_sendable(answer, settings.operation_auto_reply_min_length):
        logger.info(
            "operation_auto_reply_skipped",
            hub_issue_id=hub.id,
            reason="answer not sendable",
            answer_len=len(answer or ""),
        )
        return False

    try:
        author_reply(db, hub.id, content=answer, authored_by="agent:ai_cs")
    except ReplySyncError as e:
        logger.warning("operation_auto_reply_author_failed", hub_issue_id=hub.id, error=str(e))
        return False

    db.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=hub.id,
            proposal={"question": question, "answer": answer, "trace_id": trace_id, "sent": True},
        )
    )
    db.commit()
    logger.info("operation_auto_reply_sent", hub_issue_id=hub.id, trace_id=trace_id)
    return True
```

> 校验：`author_reply` 内部已 commit（reply_sync.py），此处末尾 db.commit() 是为 AgentDecision。若 author_reply 的 commit 与后续 add 冲突，把 AgentDecision 挪到 author_reply 前 add、一起 commit。实现时验证。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/agents/operation_answer.py backend/tests/unit/services/test_operation_answer.py
git commit -m "feat(auto-reply): operation_answer 服务 — ai_cs replay + 硬判 + author_reply"
```

---

### Task 3: 接入 webhooks _route_by_type

**Files:**
- Modify: `backend/app/api/webhooks.py`（`_route_by_type`）
- Test: `backend/tests/unit/api/test_pipeline_routing.py`（若无则新建）

**Interfaces:**
- Consumes: `auto_answer_operation`（Task 2）、`create_hub_issue_for_ticket_auto`
- Produces: Operation 毕业后自动触发答复（enabled 时）

- [ ] **Step 1: 写失败测试**

```python
# 追加/新建 backend/tests/unit/api/test_pipeline_routing.py
from unittest.mock import patch
from app.api.webhooks import _route_by_type


def test_route_operation_triggers_auto_answer():
    with patch("app.api.webhooks.get_settings") as gs, \
         patch("app.api.webhooks.create_hub_issue_for_ticket_auto") as mk, \
         patch("app.api.webhooks.auto_answer_operation") as aa:
        s = gs.return_value
        s.hub_issue_auto_enabled = True
        s.operation_auto_reply_enabled = True
        mk.return_value = type("R", (), {"created": True, "hub_issue_id": 5, "type": "Operation"})()
        _route_by_type(1, "Operation", 0.9, bar=0.8)
        aa.assert_called_once()


def test_route_operation_auto_answer_off_not_called():
    with patch("app.api.webhooks.get_settings") as gs, \
         patch("app.api.webhooks.create_hub_issue_for_ticket_auto") as mk, \
         patch("app.api.webhooks.auto_answer_operation") as aa:
        s = gs.return_value
        s.hub_issue_auto_enabled = True
        s.operation_auto_reply_enabled = False
        mk.return_value = type("R", (), {"created": True, "hub_issue_id": 5, "type": "Operation"})()
        _route_by_type(1, "Operation", 0.9, bar=0.8)
        aa.assert_not_called()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_pipeline_routing.py -v`
Expected: FAIL（auto_answer_operation 未 import / 未调用）

- [ ] **Step 3: 改 _route_by_type + import**

`webhooks.py` 顶部 import：
```python
from app.services.agents.operation_answer import auto_answer_operation
```

改 `_route_by_type`：
```python
def _route_by_type(
    ticket_id: int, ticket_type: str | None, confidence: float, *, bar: float
) -> None:
    """ADR-0016：按类型分流。Complaint 停 ticket 层转人工（不毕业）；其余在置信度
    过门槛时毕业 hub_issue（Bug/Demand 内部 hub_dedup+推 Linear；Operation 尝试自动答复）。"""
    settings = get_settings()
    if ticket_type == "Complaint":
        return  # 投诉停 ticket 层，进工作台高亮人工队列
    if not (settings.hub_issue_auto_enabled and confidence >= bar):
        return
    result = create_hub_issue_for_ticket_auto(ticket_id)
    if (
        result is not None
        and result.created
        and result.type == "Operation"
        and settings.operation_auto_reply_enabled
    ):
        db = make_session()
        try:
            auto_answer_operation(db, result.hub_issue_id, settings=settings)
        finally:
            db.close()
```

> 注：escalation(ai_cs) 来源判断在 `auto_answer_operation` 内部做（查关联 ticket source_code），此处不重复。`_route_by_type` 是普通源入库链调用（run_post_ingest_agents）；escalation 走 run_escalation_agents 不经此路，双重保险。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_pipeline_routing.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/webhooks.py backend/tests/unit/api/test_pipeline_routing.py
git commit -m "feat(auto-reply): 接入 _route_by_type — Operation 毕业后自动答复"
```

---

### Task 4: 全量验证 + lint

- [ ] **Step 1: 全量单测**

Run: `cd backend && .venv/bin/pytest -m "not integration and not e2e and not eval" -q 2>&1 | tail -15`
Expected: 全绿（GLM test_network_error 既有 flaky 可忽略）

- [ ] **Step 2: lint + 类型**

Run: `cd backend && make lint 2>&1 | tail -15`
Expected: ruff + mypy 通过（新文件如需 format 先 `ruff format`）

- [ ] **Step 3: 修复后提交**

```bash
git add -A && git commit -m "chore(auto-reply): lint + format 修复" || echo "无改动"
```

---

### Task 5: 合并 + SIT 部署 + 灰度验证

- [ ] **Step 1: 合并 main + push**

```bash
git checkout main && git merge --no-ff feat/operation-auto-reply
git push origin main
```

- [ ] **Step 2: SIT 拉代码重建**

```bash
ssh root@sit "cd /data/hub-issue && git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build"
```

- [ ] **Step 3: 健康检查**

```bash
ssh root@sit "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health"
```
Expected: 200

- [ ] **Step 4: SIT 灰度验证（用户控制 .env）**

无迁移。灰度剧本：
1. `.env` 翻 `OPERATION_AUTO_REPLY_ENABLED=true`（保持 writeback dry_run）→ 重启
2. 造/用一条 Operation 工单毕业（hub_issue_auto 开 或 手动 create-hub-issue type=Operation）
3. 观察：hub_issue.reply_content 是否被 agent 自动填、authored_by=agent:ai_cs、agent_decisions auto_reply 行
4. outbox 生成后，出站真发仍受 ksm/zhichi_writeback 灰度控制
5. 观察一批答复质量 → 决定是否加 LLM 判断层（非目标，后续）

---

## Self-Review

**Spec 覆盖**：§3 流程→Task 2；§4 硬判→Task 2 `_is_answer_sendable`；§5 config→Task 1；接入→Task 3；§8 测试→各 Task 内嵌 + Task 4；灰度→Task 5。全覆盖。

**占位扫描**：无 TODO，每步完整代码。

**类型一致性**：`auto_answer_operation(db, hub_issue_id, *, settings)` Task2 定义、Task3 调用一致；`_is_answer_sendable(answer, min_length)` 一致；`author_reply(db, hub_issue_id, *, content, authored_by)` 与现有签名一致；decision_type="auto_reply" 与 CHECK 一致。

**实现时需校验点**：
1. author_reply 内部 commit 与 Task2 末尾 AgentDecision commit 的事务顺序（Step 3 已标注）
2. `test_pipeline_routing.py` 是否已存在（盘点见有此文件，追加而非新建）
3. make_session 在 webhooks.py 已 import（现有 _route_child 用了，确认）
