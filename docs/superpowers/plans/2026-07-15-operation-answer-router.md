# Operation answer-router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在批次4️⃣ Operation 自动答复基础上，加 answer-router LLM 判 C/D/转人工——能答则 author_reply 回写关单，信息不足则 request_supply 自动补料，答不了则留主管。

**Architecture:** `operation_answer.py` 的 replay 后，新增 `_route_answer(question, answer)` 调 answer-router LLM（新 prompt，三槽）返回 `{branch, supply_note}`；按 branch 走 author_reply（D）/ request_supply（C）/ 留主管（transfer）。全部复用现有出站链。

**Tech Stack:** Python 3.11/3.12 / FastAPI / SQLAlchemy / pytest。复用 `LLMRouter`（triage 同款）+ `ai_cs.replay` + `author_reply` + `request_supply` + skill_prompts 三槽。

## Global Constraints

- answer-router 只判 **C/D/transfer**（不判 bug/需求，triage 已分 Operation）。
- LLM 调用仿 triage：`LLMRouter.from_settings()` + `load_prompt("answer_router")` + `response_format={"type":"json_object"}` + temperature=0。
- 判断失败/异常/非法 branch → 兜底 transfer（留主管，绝不瞎答）。
- D 走 `author_reply(db, hub_id, content=, authored_by="agent:ai_cs")`；C 走 `request_supply(db, hub_id, note=supply_note, requested_by="agent:ai_cs")`。
- escalation(ai_cs) 来源不走此路；operation_auto_reply_enabled 默认关。
- 出站真发受 ksm/zhichi_writeback 二层灰度。
- 分支 `feat/operation-answer-router`（已建，spec 已提交）。每 Task 末尾 commit。

---

### Task 1: answer_router prompt + 导入

**Files:**
- Create: `backend/prompts/answer_router.md`
- Test: `backend/tests/unit/services/test_prompt_store.py`（若有 import 测试则追加，否则跳过测试仅验证文件存在）

**Interfaces:**
- Produces: `prompts/answer_router.md`，`load_prompt("answer_router")` 可加载

- [ ] **Step 1: 写 prompt 文件**

```markdown
# answer-router：判定 agent 答复走向（C/D/转人工）

你是工单答复路由器。已知这是一条**运营类（Operation）**工单——类型已分好，你**不需要**再判断是不是 bug 或需求。

输入：客户原始问题 + AI 客服 agent 对该问题的答复。
你的任务：判断这次 agent 答复应该走哪个方向，只在三选一：

- **D（正常答复）**：agent 给出了客户可直接使用的有效解答（明确的操作步骤，或确定的结论性回答）。答复能解决或明确回应客户问题。
- **C（信息不足，补料）**：agent 答复表明**需要客户提供更多信息**才能精准回答（缺少截图、日志、复现步骤、具体报错信息、环境等）。这种情况下你要生成一段**面向客户的补料请求文案**（礼貌、具体说明需要什么）。
- **transfer（转人工）**：agent 明确无法回答 / 要求转人工 / 答复空泛无实质内容 / 答非所问。

判定原则：拿不准时选 **transfer**（宁可转人工，不要瞎答或瞎补料）。

只输出 JSON，格式：
```json
{"branch": "D", "supply_note": ""}
```
- branch 为 "D" | "C" | "transfer"
- 仅当 branch="C" 时 supply_note 为面向客户的补料请求文案；否则为空串
```

- [ ] **Step 2: 验证可加载**

Run: `cd backend && .venv/bin/python -c "import os; os.environ['PG_DSN']='sqlite://'; from app.services.skills.prompt_store import load_prompt; print(load_prompt('answer_router')[:40])"`
Expected: 打印 prompt 开头（load_prompt 文件回退能读到）。若 load_prompt 依赖 DB，改为直接断言文件存在：`test -f prompts/answer_router.md && echo OK`

- [ ] **Step 3: 提交**

```bash
git add backend/prompts/answer_router.md
git commit -m "feat(answer-router): 新增 answer_router prompt（判 C/D/transfer）"
```

---

### Task 2: _route_answer — answer-router LLM 调用

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py`
- Test: `backend/tests/unit/services/test_operation_answer.py`

**Interfaces:**
- Consumes: `LLMRouter`/`LLMMessage`/`LLMRouterError`（core.llm_router）、`load_prompt`（prompt_store）
- Produces: `AnswerRoute`(dataclass: branch:str, supply_note:str)、`_route_answer(question, answer, *, router=None) -> AnswerRoute`（异常/非法→branch="transfer"）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_operation_answer.py
from app.services.agents.operation_answer import _route_answer, AnswerRoute


class _FakeRouter:
    def __init__(self, content: str, raise_err: bool = False):
        self._content = content
        self._raise = raise_err
    def complete(self, messages, **kw):
        if self._raise:
            from app.core.llm_router import LLMRouterError
            raise LLMRouterError("boom")
        from types import SimpleNamespace
        return SimpleNamespace(content=self._content, cost_usd=0.0, model="fake")


def test_route_answer_d():
    r = _route_answer("开票失败", "请在设置页重新绑定后重试。",
                      router=_FakeRouter('{"branch":"D","supply_note":""}'))
    assert r.branch == "D"


def test_route_answer_c_with_supply_note():
    r = _route_answer("开票失败", "需要更多信息",
                      router=_FakeRouter('{"branch":"C","supply_note":"请提供开票报错截图"}'))
    assert r.branch == "C"
    assert r.supply_note == "请提供开票报错截图"


def test_route_answer_transfer():
    r = _route_answer("x", "无法回答",
                      router=_FakeRouter('{"branch":"transfer","supply_note":""}'))
    assert r.branch == "transfer"


def test_route_answer_llm_error_falls_back_transfer():
    r = _route_answer("x", "y", router=_FakeRouter("", raise_err=True))
    assert r.branch == "transfer"


def test_route_answer_illegal_branch_falls_back_transfer():
    r = _route_answer("x", "y", router=_FakeRouter('{"branch":"A","supply_note":""}'))
    assert r.branch == "transfer"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -k route_answer -v`
Expected: FAIL（_route_answer/AnswerRoute 未定义）

- [ ] **Step 3: 实现 _route_answer**

`operation_answer.py` 加：
```python
import json
from dataclasses import dataclass

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.services.skills.prompt_store import load_prompt

_VALID_BRANCHES = frozenset({"C", "D", "transfer"})


@dataclass(slots=True, frozen=True)
class AnswerRoute:
    branch: str  # "C" | "D" | "transfer"
    supply_note: str = ""


def _route_answer(question: str, answer: str, *, router: LLMRouter | None = None) -> AnswerRoute:
    """answer-router LLM 判 C/D/transfer。异常/非法一律兜底 transfer。"""
    try:
        prompt = load_prompt("answer_router")
        router = router or LLMRouter.from_settings()
        resp = router.complete(
            [
                LLMMessage(role="system", content=prompt),
                LLMMessage(
                    role="user",
                    content=f"客户问题：{question}\n\nagent 答复：{answer}",
                ),
                LLMMessage(role="user", content="只输出 JSON。"),
            ],
            agent="answer_router",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.content)
        branch = str(data.get("branch") or "").strip()
        if branch not in _VALID_BRANCHES:
            return AnswerRoute(branch="transfer")
        return AnswerRoute(branch=branch, supply_note=str(data.get("supply_note") or "").strip())
    except (LLMRouterError, json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning("answer_router_failed", error=str(e))
        return AnswerRoute(branch="transfer")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -k route_answer -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/agents/operation_answer.py backend/tests/unit/services/test_operation_answer.py
git commit -m "feat(answer-router): _route_answer LLM 判 C/D/transfer + 兜底"
```

---

### Task 3: auto_answer_operation 主流程接入 C/D/transfer

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py`
- Test: `backend/tests/unit/services/test_operation_answer.py`

**Interfaces:**
- Consumes: `_route_answer`（Task 2）、`author_reply`、`request_supply`（supply_sync）
- Produces: `auto_answer_operation` 改为：replay → _route_answer → D=author_reply / C=request_supply / transfer=留主管

- [ ] **Step 1: 写失败测试**

```python
# 追加：mock replay + mock _route_answer，断言 D/C/transfer 各走对分支
from unittest.mock import patch


def test_auto_answer_d_calls_author_reply(db_session):
    hub, _t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(answer="请在【发票管理】重新开票")
    route = AnswerRoute(branch="D")
    with patch("app.services.agents.operation_answer.build_client", return_value=fake), \
         patch("app.services.agents.operation_answer._route_answer", return_value=route):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 1  # author_reply 发了


def test_auto_answer_c_calls_request_supply(db_session):
    hub, t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(answer="需要更多信息")
    route = AnswerRoute(branch="C", supply_note="请提供开票报错截图")
    with patch("app.services.agents.operation_answer.build_client", return_value=fake), \
         patch("app.services.agents.operation_answer._route_answer", return_value=route):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 0  # 没答复，走的补料
    from app.models import SyncOutbox
    ob = db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id, kind="supply").first()
    assert ob is not None  # 补料 outbox 入队


def test_auto_answer_transfer_leaves_to_human(db_session):
    hub, _t = _seed_op_hub(db_session); db_session.commit()
    fake = _FakeClient(answer="无法回答")
    route = AnswerRoute(branch="transfer")
    with patch("app.services.agents.operation_answer.build_client", return_value=fake), \
         patch("app.services.agents.operation_answer._route_answer", return_value=route):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is False
    db_session.refresh(hub)
    assert hub.reply_content_version == 0
```

> 注：现有批次4️⃣ 测试 `test_auto_answer_sends`/`test_auto_answer_empty_leaves_to_human` 依赖旧的 `_is_answer_sendable` 二元逻辑，Task 3 改主流程后这些会失效——需同步更新：给它们补 `_route_answer` 的 mock（sends→D、empty→transfer），或删除被 C/D/transfer 用例取代的。Step 3 一并处理。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: 新用例 FAIL（主流程还没接 _route_answer）

- [ ] **Step 3: 改 auto_answer_operation 主流程**

替换 `_is_answer_sendable` 硬判段为 _route_answer + 分支执行：
```python
    # (replay 拿到 answer 后)
    route = _route_answer(question, answer)
    if route.branch == "D":
        try:
            author_reply(db, hub.id, content=answer, authored_by="agent:ai_cs")
        except ReplySyncError as e:
            logger.warning("operation_auto_reply_author_failed", hub_issue_id=hub.id, error=str(e))
            return False
        _record_decision(db, hub.id, branch="D", question=question, answer=answer, supply_note="")
        logger.info("operation_auto_reply_sent", hub_issue_id=hub.id)
        return True
    if route.branch == "C":
        note = route.supply_note or answer
        try:
            request_supply(db, hub.id, note=note, requested_by="agent:ai_cs")
        except SupplySyncError as e:
            logger.warning("operation_auto_supply_failed", hub_issue_id=hub.id, error=str(e))
            return False
        _record_decision(db, hub.id, branch="C", question=question, answer=answer, supply_note=note)
        logger.info("operation_auto_supply_sent", hub_issue_id=hub.id)
        return True
    # transfer → 留主管
    logger.info("operation_auto_reply_transfer", hub_issue_id=hub.id)
    return False
```
- import：`from app.services.cascade.supply_sync import SupplySyncError, request_supply`
- 加 `_record_decision(db, hub_id, *, branch, question, answer, supply_note)` 写 agent_decisions（auto_reply，proposal 带 branch），内部 db.commit()
- 删除旧 `_is_answer_sendable`（若无其他引用；测试里的 `test_sendable_rules` 一并删）
- 更新旧用例：`test_auto_answer_sends` 加 `_route_answer` mock 返回 D；`test_auto_answer_empty_leaves_to_human` 改用 transfer（或删，被新用例覆盖）

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/agents/operation_answer.py backend/tests/unit/services/test_operation_answer.py
git commit -m "feat(answer-router): auto_answer 接入 C/D/transfer — D答复 C补料 transfer留主管"
```

---

### Task 4: 全量验证 + lint

- [ ] **Step 1: 全量单测**

Run: `cd backend && .venv/bin/pytest -m "not integration and not e2e and not eval" -q 2>&1 | tail -12`
Expected: 全绿（GLM test_network_error 既有 flaky 忽略）

- [ ] **Step 2: lint + 类型**

Run: `cd backend && make lint 2>&1 | tail -12`
Expected: ruff + mypy 通过（新增代码如需 format 先 `ruff format`）

- [ ] **Step 3: 修复后提交**

```bash
git add -A && git commit -m "chore(answer-router): lint + format" || echo "无改动"
```

---

### Task 5: 从文件导入 answer_router prompt 到 skill_prompts（SIT）+ 合并部署

**Files:** 无代码改动，部署 + prompt 导入

- [ ] **Step 1: 合并 main + push**

```bash
git checkout main && git merge --no-ff feat/operation-answer-router
git push origin main
```

- [ ] **Step 2: SIT 拉代码重建**

```bash
ssh root@sit "cd /data/hub-issue && git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build"
```

- [ ] **Step 3: 导入 answer_router prompt 到 skill_prompts 三槽**

answer_router prompt 需进 skill_prompts 表（load_prompt 优先读 DB current 槽，文件是回退）。用管理后台「从文件导入」或脚本导入。SIT 上确认：
```bash
ssh root@sit "cd /data/hub-issue && docker compose -f deploy/docker-compose.sit.yml exec -T backend python -c \"
from app.db import make_session
from app.services.skills.prompt_store import load_prompt
with make_session() as db:
    print(load_prompt('answer_router')[:50])
\""
```
Expected: 打印 prompt 开头（文件回退或已入库均可）

- [ ] **Step 4: 健康检查**

```bash
ssh root@sit "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health"
```
Expected: 200

- [ ] **Step 5: SIT 灰度验证（用户控制 .env）**

无迁移。灰度剧本：
1. `.env` 翻 `OPERATION_AUTO_REPLY_ENABLED=true` + 配 `DASHSCOPE_API_KEY`（answer-router LLM 要用，或 GLM 兜底）→ 重启
2. 造/毕业一条 Operation 工单 → 观察 agent 答复 → answer-router 判 D/C/transfer：
   - D → hub.reply_content 被填、outbox reply
   - C → outbox supply + supply_note
   - transfer → 留主管
3. 出站真发受 ksm/zhichi_writeback 灰度控制
4. 观察 answer-router 判定准确率 → 不准则在管理后台调 answer_router skill 的 draft（三槽）

---

## Self-Review

**Spec 覆盖**：§4 prompt→Task 1；§4 LLM 调用→Task 2；§3 主流程 C/D/transfer→Task 3；§8 测试→各 Task；灰度部署→Task 5。全覆盖。

**占位扫描**：无 TODO，每步完整代码。

**类型一致性**：`AnswerRoute(branch, supply_note)` Task2 定义 Task3 用；`_route_answer(question, answer, *, router)` 一致；`author_reply(db, hub_id, *, content, authored_by)` / `request_supply(db, hub_id, *, note, requested_by)` 与现有签名一致；branch 值域 C/D/transfer 全程一致。

**实现时校验点**：
1. `LLMRouter.complete` 返回对象的 `.content` 字段名（Task2 _FakeRouter 用 SimpleNamespace(content=)，需与真实一致——triage/hub_dedup 用 resp.content，已确认）
2. 批次4️⃣ 旧测试（test_auto_answer_sends/empty/sendable_rules）随主流程改造需同步更新（Task3 Step3 已标注）
3. `request_supply` 内部已 commit（supply_sync.py），_record_decision 的 commit 顺序（放 author_reply/request_supply 之后）
