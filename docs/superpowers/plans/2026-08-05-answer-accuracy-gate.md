# Operation 答复准确率闸门 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Operation 自动答复链加一个准确率打分器 + 三态闸门（off/observe/enforce），低置信答复存草稿转主管审核，主管编辑后发送。

**Architecture:** 在 `answer_router` 判 D 之后插一步独立 LLM 打分（照抄 `_route_answer` 骨架）。三态模式：`off`=不打分同现状；`observe`=打分记审计但照常直发；`enforce`=低置信存草稿(reply_content + reply_is_draft 标记，不入 outbox) + op_status=reviewing + 转兜底主管。主管走现有 `POST /reply` 编辑后发送。新增 op_status `reviewing`（6 态）。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + pytest（SQLite in-memory / StaticPool）。命令在 `backend/`：`.venv/bin/pytest <path> -v`，`make lint`。

**Spec:** `docs/superpowers/specs/2026-08-05-answer-accuracy-gate-design.md`

## Global Constraints

- 所有后端命令在 `backend/` 目录下；测试 `.venv/bin/pytest <path> -v`；单测走 SQLite in-memory（`db_session` fixture），schema 由 `app/models.py` metadata 建（不跑迁移）。
- op_status 变更一律经 `apply_op_status`（唯一入口），不直接赋值。
- 兜底主管名用 `resolve_supervisor_name(db, settings)`。
- prompt 用 `load_prompt("<name>")`（DB 覆盖 → `prompts/<name>.md` 文件兜底）。
- LLM 打分器异常/非法 JSON 一律兜底 `accuracy=0`（安全侧：打分失败视作低置信）。
- 三态模式取值：`off`（默认）/ `observe` / `enforce`。阈值默认 `90`。
- **草稿绝不入 outbox、绝不级联**（不发客户）；只有主管 `/reply` 才真正发。
- 迁移编号 **0028**（0027 已被 feishu_ai 占用），down_revision=`0027_feishu_ai_source`（确认其真实 revision id，见 Task 1）。
- 现有 answer_router 判 D/C/transfer 逻辑、C/transfer 分支、研发类路径、closed 拒绝、T+7 自动关一律不动。

---

### Task 1: 新增 op_status `reviewing` + `reply_is_draft` 字段 + 迁移 0028

状态常量、约束、草稿标记字段、迁移。地基任务。

**Files:**
- Modify: `backend/app/services/hub_issues/op_status.py`（加 OP_REVIEWING）
- Modify: `backend/app/models.py`（op_status 约束加 reviewing；HubIssue 加 reply_is_draft 列）
- Create: `backend/migrations/versions/0028_reviewing_and_reply_draft.py`
- Test: `backend/tests/unit/test_models_op_status.py`

**Interfaces:**
- Produces: `op_status.py` 导出 `OP_REVIEWING = "reviewing"`；`_VALID` 含 reviewing。`HubIssue.reply_is_draft: bool`（默认 False）。

- [ ] **Step 1: 确认 0027 的真实 revision id**

Run: `grep -n "^revision" backend/migrations/versions/0027_feishu_ai_source.py`
记下实际 revision 字符串（如 `0027_feishu_ai_source`），用作 0028 的 down_revision。**不要假设文件名==revision id**（上一个迁移就踩过这个坑）。

- [ ] **Step 2: 写失败测试**

在 `backend/tests/unit/test_models_op_status.py` 加：

```python
def test_reviewing_accepted_by_constraint(db_session) -> None:
    """迁移后 op_status='reviewing' 应被接受。"""
    from app.models import HubIssue

    hub = HubIssue(
        short_code="HUB-RVW-1", type="Operation", title="t",
        status="created", op_status="reviewing", op_handler="主管",
    )
    db_session.add(hub)
    db_session.flush()  # 不抛 = 通过
    assert hub.op_status == "reviewing"


def test_reply_is_draft_defaults_false(db_session) -> None:
    from app.models import HubIssue

    hub = HubIssue(short_code="HUB-DFT-1", type="Operation", title="t", status="created")
    db_session.add(hub)
    db_session.flush()
    assert hub.reply_is_draft is False
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py::test_reviewing_accepted_by_constraint tests/unit/test_models_op_status.py::test_reply_is_draft_defaults_false -v`
Expected: FAIL（约束不含 reviewing → IntegrityError；reply_is_draft 列不存在 → AttributeError/OperationalError）

- [ ] **Step 4: 加常量**

`app/services/hub_issues/op_status.py`：加 `OP_REVIEWING = "reviewing"`，`_VALID` frozenset 加入 `OP_REVIEWING`：

```python
OP_REVIEWING = "reviewing"

_VALID = frozenset(
    {OP_PROCESSING, OP_ANSWERED, OP_CLOSED, OP_SUPPLEMENTING, OP_REVIEWING, OP_EXCEPTION}
)
```

- [ ] **Step 5: 改 model 约束 + 加列**

`app/models.py`：op_status CheckConstraint 加 `reviewing`：

```python
        CheckConstraint(
            "op_status IS NULL OR op_status IN "
            "('processing','answered','closed','supplementing','reviewing','exception')",
            name="ck_hub_issues_op_status",
        ),
```

在 HubIssue 的 Operation 字段区（`reply_authored_by` / `reply_updated_at` 附近）加列：

```python
    reply_is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

（确认 `Boolean` 已从 sqlalchemy import；若无则加。）

- [ ] **Step 6: 写迁移 0028**

`backend/migrations/versions/0028_reviewing_and_reply_draft.py`（`<REV_0027>` 替换为 Step 1 查到的真实 id）：

```python
"""add reviewing op_status + reply_is_draft

Revision ID: 0028_reviewing_and_reply_draft
Revises: <REV_0027>
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0028_reviewing_and_reply_draft"
down_revision: str | None = "<REV_0027>"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','reviewing','exception')"
)
_OLD = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','exception')"
)


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("reply_is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _OLD)
    op.drop_column("hub_issues", "reply_is_draft")
```

- [ ] **Step 7: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py -v`
Expected: PASS

- [ ] **Step 8: 验证迁移链**

Run: `.venv/bin/alembic heads`
Expected: 打印 `0028_reviewing_and_reply_draft (head)`，无 KeyError。若报错检查 down_revision 是否匹配 Step 1 的真实 id。

- [ ] **Step 9: 提交**

```bash
git add app/services/hub_issues/op_status.py app/models.py \
        migrations/versions/0028_reviewing_and_reply_draft.py \
        tests/unit/test_models_op_status.py
git commit -m "feat(op_status): 加 reviewing 态 + reply_is_draft 字段 + 迁移 0028"
```

---

### Task 2: 准确率打分器 + prompt

独立 LLM 打分器，照抄 `_route_answer` 骨架。

**Files:**
- Create: `backend/app/services/agents/answer_accuracy.py`
- Create: `backend/prompts/answer_accuracy.md`
- Test: `backend/tests/unit/services/test_answer_accuracy.py`

**Interfaces:**
- Produces: `score_answer_accuracy(question: str, answer: str, cited_knowledge: list[dict], *, router: LLMRouter | None = None) -> AccuracyScore`；`AccuracyScore(accuracy: int, reason: str)`（frozen dataclass）。异常/非法 → `AccuracyScore(accuracy=0, reason=...)`。

- [ ] **Step 1: 写失败测试**

`backend/tests/unit/services/test_answer_accuracy.py`：

```python
from __future__ import annotations

from types import SimpleNamespace

from app.services.agents.answer_accuracy import AccuracyScore, score_answer_accuracy


class _FakeRouter:
    def __init__(self, content: str, raise_err: bool = False) -> None:
        self._content = content
        self._raise = raise_err

    def complete(self, messages: object, **kw: object) -> object:
        if self._raise:
            from app.core.llm_router import LLMRouterError

            raise LLMRouterError("boom")
        return SimpleNamespace(content=self._content, cost_usd=0.0, model="fake")


def test_score_parses_accuracy_and_reason() -> None:
    r = score_answer_accuracy(
        "开票失败怎么办",
        "请在发票管理页重新发起。",
        [{"title": "开票指引", "content": "在发票管理页重新发起开票"}],
        router=_FakeRouter('{"accuracy": 95, "reason": "与知识库一致"}'),
    )
    assert isinstance(r, AccuracyScore)
    assert r.accuracy == 95
    assert "一致" in r.reason


def test_score_llm_error_defaults_zero() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter("", raise_err=True)
    )
    assert r.accuracy == 0


def test_score_invalid_json_defaults_zero() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter("not json")
    )
    assert r.accuracy == 0


def test_score_out_of_range_clamped() -> None:
    r = score_answer_accuracy(
        "x", "y", [], router=_FakeRouter('{"accuracy": 150, "reason": "r"}')
    )
    assert 0 <= r.accuracy <= 100
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_answer_accuracy.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 prompt**

`backend/prompts/answer_accuracy.md`：

```markdown
你是一个客服答复质量评估器。给定「客户问题」「AI 答复」和「引用的知识条目」，评估这条答复的**准确率**（0-100 整数）。

综合依据：
1. 答复是否与引用的知识条目一致、有据可循
2. 结合你对该业务领域的理解，答复是否事实正确
3. 是否答非所问、答复是否偏离问题
4. 是否含臆造/无依据的信息

评分标准：
- 90-100：答复准确、有知识依据、直接解决问题
- 70-89：基本正确但有小瑕疵或依据不充分
- <70：有明显错误、答非所问、或缺乏依据

只输出 JSON，不要任何其他文字：
{"accuracy": <0-100 整数>, "reason": "<简短中文理由，一句话>"}
```

- [ ] **Step 4: 写实现**

`backend/app/services/agents/answer_accuracy.py`（镜像 `_route_answer`）：

```python
"""Operation 答复准确率打分器（独立 LLM 调用，仿 answer_router）.

在 answer_router 判 D 之后对答复打分（0-100），依据知识库+FAQ 综合判断。
异常/非法 JSON 一律兜底 accuracy=0（安全侧：打分失败视作低置信转主管）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.services.skills.prompt_store import load_prompt

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class AccuracyScore:
    accuracy: int  # 0-100
    reason: str = ""


def score_answer_accuracy(
    question: str,
    answer: str,
    cited_knowledge: list[dict],
    *,
    router: LLMRouter | None = None,
) -> AccuracyScore:
    """LLM 打分。异常/非法一律兜底 accuracy=0（低置信留主管）。"""
    try:
        prompt = load_prompt("answer_accuracy")
        router = router or LLMRouter.from_settings()
        cited_text = json.dumps(cited_knowledge, ensure_ascii=False)
        resp = router.complete(
            [
                LLMMessage(role="system", content=prompt),
                LLMMessage(
                    role="user",
                    content=f"客户问题：{question}\n\nAI 答复：{answer}\n\n引用知识：{cited_text}",
                ),
                LLMMessage(role="user", content="只输出 JSON。"),
            ],
            agent="answer_accuracy",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.content)
        raw = int(data.get("accuracy"))
        accuracy = max(0, min(100, raw))  # clamp
        return AccuracyScore(accuracy=accuracy, reason=str(data.get("reason") or ""))
    except (LLMRouterError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning("answer_accuracy_scoring_failed", error=str(e))
        return AccuracyScore(accuracy=0, reason="打分失败，兜底转主管")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_answer_accuracy.py -v`
Expected: PASS（4 条）

- [ ] **Step 6: 提交**

```bash
git add app/services/agents/answer_accuracy.py prompts/answer_accuracy.md \
        tests/unit/services/test_answer_accuracy.py
git commit -m "feat(agents): 新增答复准确率打分器 answer_accuracy"
```

---

### Task 3: 配置项（三态模式 + 阈值）

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/unit/test_config.py`（若存在同类测试则加；否则本任务并入 Task 4 验证，跳过独立测试）

**Interfaces:**
- Produces: `settings.operation_answer_accuracy_mode: str = "off"`、`settings.operation_answer_accuracy_threshold: int = 90`。

- [ ] **Step 1: 加配置**

`app/config.py` operation 段（`operation_auto_close_days` 附近）加：

```python
    # 答复准确率闸门：off=不打分同现状 / observe=打分记审计但照常直发（采集分布）
    # / enforce=低置信存草稿转主管审核
    operation_answer_accuracy_mode: str = "off"
    operation_answer_accuracy_threshold: int = 90  # 0-100，仅 enforce 生效
```

- [ ] **Step 2: 验证可加载**

Run: `.venv/bin/python -c "from app.config import get_settings; s=get_settings(); print(s.operation_answer_accuracy_mode, s.operation_answer_accuracy_threshold)"`
Expected: 打印 `off 90`

- [ ] **Step 3: 提交**

```bash
git add app/config.py
git commit -m "feat(config): 加答复准确率三态模式 + 阈值配置"
```

---

### Task 4: D 分支接入闸门 + 草稿存储 + replay 带出 cited_knowledge

核心改造。`_replay_with_retry` 改为带出 cited_knowledge；D 分支按三态模式分流；新增 `_save_draft_reply`；`_record_decision` 加 extra。

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py`
- Test: `backend/tests/unit/services/test_operation_answer.py`

**Interfaces:**
- Consumes: `score_answer_accuracy`, `AccuracyScore`（Task 2）；`OP_REVIEWING`（Task 1）；`settings.operation_answer_accuracy_mode/threshold`（Task 3）。
- Produces: `_save_draft_reply(db, hub, *, content: str)` — 写 `hub.reply_content=content` + `reply_is_draft=True` + `reply_authored_by="agent:ai_cs:draft"`，**不级联不入 outbox**，不 commit。`_record_decision(..., extra: dict | None = None)`。

- [ ] **Step 1: 改 `_replay_with_retry` 带出 cited_knowledge（先改测试期望）**

现状 `_replay_with_retry` 返回 `str`（只 answer）。改为返回 `ReplayResult`。`_FakeClient.replay` 已返回完整 `ReplayResult`（test 文件 line 35-39），故 fake 无需改。先加一条针对新签名的测试到 `test_operation_answer.py`：

```python
def test_replay_with_retry_returns_cited_knowledge(db_session: Session) -> None:
    """_replay_with_retry 返回完整 ReplayResult（带 cited_knowledge）供打分器用。"""
    from app.services.agents.operation_answer import _replay_with_retry

    fake = _FakeClient(answer="答复内容")
    result = _replay_with_retry(fake, question="q", skill=None, hub_id=1)
    assert result.answer == "答复内容"
    assert hasattr(result, "cited_knowledge")
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py::test_replay_with_retry_returns_cited_knowledge -v`
Expected: FAIL（当前返回 str，无 `.answer`/`.cited_knowledge`）

- [ ] **Step 3: 改 `_replay_with_retry` 返回类型**

`operation_answer.py`：把 `_replay_with_retry` 的返回从 `str(result.answer)` 改为返回整个 `result`（`ReplayResult`）。签名 `-> ReplayResult`。import `from adapters.ai_cs.types import ReplayResult`（或 `from adapters.ai_cs import ReplayResult`，按现有 import 风格）。

同步改调用点（line 203）：
```python
    try:
        replay_result = _replay_with_retry(client, question=question, skill=skill, hub_id=hub.id)
    except AiCsError:
        ...  # exception 分支不变
    finally:
        client.close()
    answer = replay_result.answer
    cited_knowledge = replay_result.cited_knowledge
```

- [ ] **Step 4: 跑确认通过（含既有回归）**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS（新测试 + 既有 D/C/transfer 全绿——answer 变量语义未变）

- [ ] **Step 5: 写闸门测试（observe + enforce 两模式）**

加到 `test_operation_answer.py`。注意 mock 掉 `score_answer_accuracy`（打分器单测已独立覆盖，这里只验分流）：

```python
def test_d_observe_mode_scores_but_sends(db_session: Session) -> None:
    """observe：打分记审计但照常直发客户 + answered。"""
    from app.models import SyncOutbox
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "observe"
    fake = _FakeClient(answer="您好，请在发票管理页重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch("app.services.agents.operation_answer._route_answer",
              return_value=AnswerRoute(branch="D", supply_note="")),
        patch("app.services.agents.operation_answer.score_answer_accuracy",
              return_value=AccuracyScore(accuracy=40, reason="低分也直发")),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "answered"          # observe 低分仍直发
    assert hub.reply_content_version >= 1       # 真的发了（走 author_reply）
    assert hub.reply_is_draft is False


def test_d_enforce_low_accuracy_saves_draft_reviewing(db_session: Session) -> None:
    """enforce + <阈值：存草稿(不发) + reviewing + 转主管 + 无 outbox。"""
    from app.models import SyncOutbox
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "enforce"
    s.operation_answer_accuracy_threshold = 90
    fake = _FakeClient(answer="可能是网络问题，建议稍后再试。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch("app.services.agents.operation_answer._route_answer",
              return_value=AnswerRoute(branch="D", supply_note="")),
        patch("app.services.agents.operation_answer.score_answer_accuracy",
              return_value=AccuracyScore(accuracy=60, reason="依据不足")),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "reviewing"
    assert hub.op_handler != "agent"            # 转兜底主管
    assert hub.reply_content == "可能是网络问题，建议稍后再试。"
    assert hub.reply_is_draft is True           # 草稿标记
    assert hub.reply_content_version == 0       # 未经 author_reply（未级联）
    # 不发客户 → 无 outbox
    assert db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id).count() == 0


def test_d_enforce_high_accuracy_sends(db_session: Session) -> None:
    """enforce + ≥阈值：直发 + answered。"""
    from app.services.agents.answer_accuracy import AccuracyScore

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    s = _S()
    s.operation_answer_accuracy_mode = "enforce"
    fake = _FakeClient(answer="您好，请在【发票管理】页重新发起开票并保存。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch("app.services.agents.operation_answer._route_answer",
              return_value=AnswerRoute(branch="D", supply_note="")),
        patch("app.services.agents.operation_answer.score_answer_accuracy",
              return_value=AccuracyScore(accuracy=95, reason="准确")),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=s)
    assert ok is True
    db_session.refresh(hub)
    assert hub.op_status == "answered"
    assert hub.reply_is_draft is False
```

注：`_S()` helper 返回 settings 对象；若它返回不可变对象无法赋属性，改用 `dataclasses.replace` 或按 `_S` 实际实现调整（读 test 文件顶部 `_S` 定义）。off 模式行为=现状，已被既有 `test_auto_answer_d_sends` 覆盖，无需新增。

- [ ] **Step 6: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -k "observe or enforce" -v`
Expected: FAIL（闸门逻辑未实现，当前 D 分支直发不打分）

- [ ] **Step 7: 实现 D 分支闸门 + `_save_draft_reply` + `_record_decision` extra**

改 `_record_decision` 签名加 `extra: dict | None = None`，把 extra 合并进 `proposal`：

```python
def _record_decision(db, hub_id, *, branch, question, answer, supply_note, extra=None):
    proposal = {"branch": branch, "question": question, "answer": answer, "supply_note": supply_note}
    if extra:
        proposal.update(extra)
    db.add(AgentDecision(decision_type="auto_reply", subject_type="hub_issue",
                         subject_id=hub_id, proposal=proposal))
    db.commit()
```

加 `_save_draft_reply`：

```python
def _save_draft_reply(db: Session, hub: HubIssue, *, content: str) -> None:
    """存 agent 草稿到 hub.reply_content 但标记未发（不级联、不入 outbox）。
    主管 POST /reply 发送时 author_reply 会清 reply_is_draft。"""
    hub.reply_content = content
    hub.reply_is_draft = True
    hub.reply_authored_by = "agent:ai_cs:draft"
```

import 加 `OP_REVIEWING`、`score_answer_accuracy`。D 分支（现 line 236-249）改为 spec §3 的三态逻辑（off/observe 直发；enforce 低置信存草稿转 reviewing）。完整代码见 spec `docs/superpowers/specs/2026-08-05-answer-accuracy-gate-design.md` §3 的代码块（`reason=f"..."` 里的 `{阈值}` 用 `settings.operation_answer_accuracy_threshold`）。

- [ ] **Step 8: 跑确认通过（含全文件回归）**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS（新 3 条 + replay 1 条 + 既有全绿）

- [ ] **Step 9: 提交**

```bash
git add app/services/agents/operation_answer.py tests/unit/services/test_operation_answer.py
git commit -m "feat(operation): D 分支接入准确率三态闸门 + 草稿存储"
```

---

### Task 5: 草稿防误发（_released_text 尊重 reply_is_draft）+ author_reply 清标记

**Files:**
- Modify: `backend/app/services/ksm/writeback.py`（`_released_text`）
- Modify: `backend/app/services/zhichi/writeback.py`（若有对称的 released 读 reply_content 路径）
- Modify: `backend/app/services/cascade/reply_sync.py`（`author_reply` 清 reply_is_draft）
- Test: `backend/tests/unit/test_hub_issue_reply_api.py`（author_reply 清标记）+ `backend/tests/unit/services/test_ksm_ingester.py` 或 writeback 测试（released 回落）

**Interfaces:**
- Consumes: `hub.reply_is_draft`（Task 1）。
- Produces: `_released_text` 仅在 `not reply_is_draft` 时用 reply_content；`author_reply` 成功后置 `reply_is_draft=False`。

- [ ] **Step 1: 写 author_reply 清标记测试**

在 `test_hub_issue_reply_api.py` 加（reply_world fixture 有 Operation hub 90）：

```python
def test_reply_clears_draft_flag(app_client, reply_world) -> None:
    """主管发送答复后 reply_is_draft 清零（草稿转正式已发）。"""
    from app.models import HubIssue

    hub = reply_world.get(HubIssue, 90)
    hub.reply_is_draft = True
    hub.op_status = "reviewing"
    reply_world.commit()

    r = app_client.post("/api/hub-issues/90/reply",
                        json={"content": "主管审核后的答复"}, headers=_bearer(2))
    assert r.status_code == 200, r.text
    reply_world.refresh(hub)
    assert hub.reply_is_draft is False
    assert hub.op_status == "answered"
    assert hub.reply_content == "主管审核后的答复"
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/test_hub_issue_reply_api.py::test_reply_clears_draft_flag -v`
Expected: FAIL（author_reply 不清 reply_is_draft，仍 True）

- [ ] **Step 3: author_reply 清标记**

`app/services/cascade/reply_sync.py` `author_reply`，在设置 reply_content 处（line 65 附近）加 `hub.reply_is_draft = False`：

```python
    hub.reply_content = content
    hub.reply_content_version = version
    hub.reply_authored_by = authored_by
    hub.reply_updated_at = now
    hub.reply_is_draft = False  # 正式发送，清草稿标记
```

- [ ] **Step 4: 写 released 防误发测试**

在 `backend/tests/unit/services/test_ksm_writeback.py`（若无则新建，参考现有 writeback 测试结构）加一条：草稿态 hub（reply_content 有值 + reply_is_draft=True）经 `_released_text` 应回落默认话术：

```python
def test_released_text_ignores_draft_reply(db_session) -> None:
    """草稿态 reply_content 不能被当 released 关单话术发出。"""
    from app.models import HubIssue
    from app.services.ksm.writeback import KSMWriteback, _DEFAULT_RELEASED_NOTE

    hub = HubIssue(short_code="HUB-DR-1", type="Operation", title="t", status="created",
                   reply_content="未审核草稿", reply_is_draft=True)
    db_session.add(hub)
    db_session.flush()
    row = SimpleNamespace(hub_issue_id=hub.id)  # 或按 _released_text 实际入参构造
    wb = KSMWriteback(db_session)  # 按实际构造签名调整
    assert wb._released_text(row) == _DEFAULT_RELEASED_NOTE
```

注：`KSMWriteback` 的类名/构造签名以 `writeback.py` 实际为准（读文件确认；可能是模块级函数而非类方法）。若 `_released_text` 是实例方法且构造复杂，可改为直接单元测其分支逻辑或用现有 writeback 测试的 fixture 模式。

- [ ] **Step 5: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_writeback.py::test_released_text_ignores_draft_reply -v`
Expected: FAIL（当前 `_released_text` 不看 reply_is_draft，返回草稿文本）

- [ ] **Step 6: 改 _released_text**

`app/services/ksm/writeback.py:429-433`：

```python
    def _released_text(self, row: SyncOutbox) -> str:
        hub = self._db.get(HubIssue, row.hub_issue_id)
        if hub is not None and hub.reply_content and not hub.reply_is_draft:
            return str(hub.reply_content).strip()
        return _DEFAULT_RELEASED_NOTE
```

检查 `zhichi/writeback.py:247-248` 是否有对称路径；若有，同样加 `and not hub.reply_is_draft`。

- [ ] **Step 7: 跑确认通过**

Run: `.venv/bin/pytest tests/unit/test_hub_issue_reply_api.py tests/unit/services/test_ksm_writeback.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add app/services/cascade/reply_sync.py app/services/ksm/writeback.py \
        app/services/zhichi/writeback.py tests/unit/test_hub_issue_reply_api.py \
        tests/unit/services/test_ksm_writeback.py
git commit -m "fix(reply): 草稿防误发（released 尊重 reply_is_draft）+ author_reply 清标记"
```

---

### Task 6: 主管审核队列 API

**Files:**
- Modify: `backend/app/api/supervisor.py`
- Test: `backend/tests/unit/test_supervisor_reviewing.py`（新建）或并入现有 supervisor 测试

**Interfaces:**
- Produces: `GET /api/supervisor/reviewing-answers`（require_supervisor）→ 列 reviewing 单 + agent 草稿 + 最近 D_review 决策的 accuracy/reason。

- [ ] **Step 1: 写测试**

`backend/tests/unit/test_supervisor_reviewing.py`（参考 `test_hub_issue_reply_api.py` 的 `_bearer` + fixture 风格）：

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def rvw_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(HubIssue(id=70, short_code="HUB-000070", type="Operation",
                            title="开票失败", status="created", op_status="reviewing",
                            op_handler="主管", reply_content="草稿答复", reply_is_draft=True))
    db_session.flush()
    db_session.add(AgentDecision(decision_type="auto_reply", subject_type="hub_issue",
                                 subject_id=70,
                                 proposal={"branch": "D_review", "accuracy": 60,
                                           "reason": "依据不足", "answer": "草稿答复"}))
    db_session.commit()
    return db_session


def test_reviewing_requires_supervisor(app_client: TestClient, rvw_world: Session) -> None:
    r = app_client.get("/api/supervisor/reviewing-answers",
                       headers=_bearer(1, name="bob", role="member"))
    assert r.status_code == 403


def test_reviewing_lists_with_accuracy(app_client: TestClient, rvw_world: Session) -> None:
    r = app_client.get("/api/supervisor/reviewing-answers", headers=_bearer(2))
    assert r.status_code == 200, r.text
    items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    assert len(items) == 1
    it = items[0]
    assert it["short_code"] == "HUB-000070"
    assert it["draft_reply"] == "草稿答复"
    assert it["accuracy"] == 60
    assert "依据不足" in (it["accuracy_reason"] or "")
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/test_supervisor_reviewing.py -v`
Expected: FAIL（端点不存在 → 404）

- [ ] **Step 3: 实现端点**

`app/api/supervisor.py`（参考 `pending-hub-issues` 端点 line 783 的结构）新增：

```python
class ReviewingAnswerItem(BaseModel):
    hub_issue_id: int
    short_code: str
    title: str
    question: str | None
    draft_reply: str | None
    accuracy: int | None
    accuracy_reason: str | None


class ReviewingAnswersResponse(BaseModel):
    items: list[ReviewingAnswerItem]


@router.get("/reviewing-answers", response_model=ReviewingAnswersResponse)
def reviewing_answers(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> ReviewingAnswersResponse:
    """待主管审核的低置信答复队列（op_status=reviewing）。"""
    hubs = (
        db.query(HubIssue)
        .filter(HubIssue.type == "Operation", HubIssue.op_status == "reviewing",
                HubIssue.deleted_at.is_(None))
        .order_by(HubIssue.id)
        .all()
    )
    items = []
    for h in hubs:
        dec = (
            db.query(AgentDecision)
            .filter(AgentDecision.subject_type == "hub_issue", AgentDecision.subject_id == h.id,
                    AgentDecision.decision_type == "auto_reply")
            .order_by(AgentDecision.id.desc())
            .first()
        )
        prop = (dec.proposal if dec else {}) or {}
        items.append(ReviewingAnswerItem(
            hub_issue_id=h.id, short_code=h.short_code, title=h.title,
            question=prop.get("question"), draft_reply=h.reply_content,
            accuracy=prop.get("accuracy"), accuracy_reason=prop.get("reason"),
        ))
    return ReviewingAnswersResponse(items=items)
```

（确认 `HubIssue`、`AgentDecision`、`AuthedUser`、`require_supervisor`、`BaseModel` 已在文件 import。）

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/pytest tests/unit/test_supervisor_reviewing.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/supervisor.py tests/unit/test_supervisor_reviewing.py
git commit -m "feat(supervisor): 新增待审核答复队列 GET /reviewing-answers"
```

---

### Task 7: 前端（reviewing 徽章 + 审核队列 + 类型同步）

**Files:**
- Modify: `frontend/src/components/OpStatusBadge.tsx`（+reviewing 徽章）
- Modify: `frontend/src/pages/.../SupervisorPage.tsx`（+待审核答复队列卡片）
- Modify: `frontend/src/api/types.ts`（gen）+ op_status 筛选项（若硬编码）

**Interfaces:** 消费 `GET /api/supervisor/reviewing-answers`；发送走现有 `POST /api/hub-issues/{id}/reply`。

- [ ] **Step 1: 重新生成 API 类型**

Run（repo 根）: `make gen-types`
Expected: `frontend/src/api/types.ts` 含 `/api/supervisor/reviewing-answers` 与 `ReviewingAnswerItem`。

- [ ] **Step 2: 加 reviewing 徽章**

`frontend/src/components/OpStatusBadge.tsx`：加一行（颜色另取一色，与现有 processing/answered/... 区分）：

```tsx
  reviewing: { label: "待审核", bg: "#e0e7ff", fg: "#3730a3", bd: "#c7d2fe" },
```

- [ ] **Step 3: 加审核队列卡片**

`SupervisorPage.tsx`：新增一个 query 拉 `/api/supervisor/reviewing-answers`，渲染卡片列表（颜色区别于紫/青/琥珀/黄）。每张卡展示 short_code、question、draft_reply（放进可编辑 textarea）、accuracy + accuracy_reason，一个「发送」按钮调 `POST /api/hub-issues/{hub_issue_id}/reply`（body `{content: 编辑后的文本}`）。发送成功后刷新队列（该单转 answered 离开队列）。

参考现有 SupervisorPage 里 pending/dedup/split 卡片的 query + mutation + 刷新写法，保持一致。

- [ ] **Step 4: 类型检查**

Run（`frontend/`）: `npm run type-check`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/OpStatusBadge.tsx frontend/src/pages frontend/src/api/types.ts frontend/src/api/openapi.json
git commit -m "feat(frontend): reviewing 徽章 + 主管待审核答复队列"
```

---

### Task 8: 全量回归 + lint + 迁移可跑性

**Files:** 无（验证）

- [ ] **Step 1: 后端全量单测**

Run（`backend/`）: `.venv/bin/pytest -q`
Expected: 全绿（除已知 pre-existing `test_glm_client.py::test_network_error`）。

- [ ] **Step 2: lint（仅本次改动文件确认干净）**

Run（`backend/`）: `.venv/bin/ruff check app/services/agents/answer_accuracy.py app/services/agents/operation_answer.py app/config.py app/models.py app/services/hub_issues/op_status.py app/services/ksm/writeback.py app/services/cascade/reply_sync.py app/api/supervisor.py migrations/versions/0028_reviewing_and_reply_draft.py` + `.venv/bin/mypy app/services/agents/answer_accuracy.py app/services/agents/operation_answer.py`
Expected: All checks passed / no issues。

- [ ] **Step 3: 前端类型 + 构建**

Run（`frontend/`）: `npm run type-check && npm run build`
Expected: PASS。

- [ ] **Step 4: 迁移可跑性（offline SQL）**

Run（`backend/`）: `.venv/bin/alembic heads` + `.venv/bin/alembic upgrade <REV_0027>:0028_reviewing_and_reply_draft --sql`
Expected: heads 干净指向 0028；SQL 含 add reply_is_draft + 约束加 reviewing。有本地 PG 则跑真实 `upgrade head` + `downgrade -1` + `upgrade head`。

- [ ] **Step 5: 提交（若有 lint 修复）**

```bash
git add -A && git commit -m "chore: 准确率闸门全量回归 + lint" || echo "无额外改动"
```

---

## Self-Review

**Spec coverage:**
- 打分器（LLM，依据知识库+FAQ，cited_knowledge）→ Task 2 ✅
- 三态模式 off/observe/enforce + 阈值可配 → Task 3 + Task 4 ✅
- observe 打分记审计照常直发 → Task 4 Step 5 `test_d_observe_mode_scores_but_sends` ✅
- enforce 低置信存草稿 + reviewing + 转主管 + 不发客户 → Task 4 `test_d_enforce_low_accuracy_saves_draft_reviewing` ✅
- 新 op_status reviewing → Task 1 ✅
- 草稿存 reply_content + reply_is_draft 标记 + 防误发 → Task 1（字段）+ Task 4（存）+ Task 5（防误发/清标记）✅
- 主管编辑后发送走 /reply → Task 5（清标记）+ Task 7（前端）；/reply 对 reviewing 无拒绝（closed 才拒）✅
- 审核队列 → Task 6 ✅
- 前端徽章 + 队列 → Task 7 ✅
- 迁移 0028（非 0027）→ Task 1 ✅

**Placeholder scan:** `<REV_0027>` 是有意占位，Task 1 Step 1 明确要求查真实 id 后替换；其余无 TBD。测试代码均可运行；helper 签名不确定处（`_S`、`KSMWriteback` 构造）已标注"以文件实际为准"并给核对方法。

**Type consistency:** `AccuracyScore(accuracy, reason)`、`score_answer_accuracy(question, answer, cited_knowledge, *, router)`、`OP_REVIEWING="reviewing"`、`reply_is_draft`、`_save_draft_reply(db, hub, *, content)`、`_record_decision(..., extra)` 全程一致。迁移 revision `0028_reviewing_and_reply_draft`，down_revision 待 Task 1 Step 1 确认。
