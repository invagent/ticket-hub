# hub_dedup 全类型覆盖 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有类型毕业 hub_issue 时都语义查重（当前只 Bug/Demand 推 Linear 时查）。命中则当前 hub supersede 到原 hub + occurrence_count+1 + ticket 挂原 hub。

**Architecture:** ①`find_duplicate_hub` 候选过滤按类型分流 + 加 type 约束；②抽 `maybe_supersede_duplicate` 到 hub_dedup 公共；③creator 毕业后调它；④linear_push 改调公共函数 + 已 superseded 跳过。方案 2 建后 supersede。

**Tech Stack:** Python 3.11/3.12 / SQLAlchemy / pytest。复用现有 hub_dedup embedding + supersede 链 + occurrence_count。

## Global Constraints

- 候选过滤：`type == hub.type`（不跨类型）；Bug/Demand 额外 `linear_uuid IS NOT NULL`；Operation/Internal_task 不要求 linear_uuid。
- 命中：当前 hub 标 `superseded_by_hub_issue_id`=原 hub + 原 hub `occurrence_count+1` + `last_seen_at`；ticket 挂**原 hub**；HubIssueResult.created=False。
- 灰度 `hub_dedup_enabled`（默认 true）；降级（embedding/LLM 失败）→ None → 建新 hub 不阻塞。
- 分支 `feat/hub-dedup-all-types`（已建，spec 已提交）。每 Task commit。

---

### Task 1: find_duplicate_hub 候选过滤按类型

**Files:**
- Modify: `backend/app/services/hub_issues/hub_dedup.py`（候选 select）
- Test: `backend/tests/unit/services/test_hub_dedup.py`（若无则新建）

**Interfaces:**
- Produces: `find_duplicate_hub` 支持 Operation/Internal_task 候选（同类型，不要求 linear_uuid）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/services/test_hub_dedup.py（新建或追加）
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.models import HubIssue
from app.services.hub_issues.hub_dedup import find_duplicate_hub


def _hub(db, *, type_, plc="发票云", linear=None, emb=None, title="开票失败"):
    h = HubIssue(short_code=f"HUB-{title}-{type_}-{linear or 'x'}", type=type_, title=title,
                 canonical_body="开票网络错误", status="created", product_line_code=plc,
                 linear_uuid=linear, embedding=emb)
    db.add(h); db.flush()
    return h


def test_operation_candidate_no_linear_required(db_session: Session):
    # 已有一个 Operation hub（无 linear_uuid，有 embedding）
    existing = _hub(db_session, type_="Operation", linear=None, emb=[1.0, 0.0])
    cur = _hub(db_session, type_="Operation", linear=None, emb=[1.0, 0.0], title="开票失败2")
    db_session.flush()
    # mock LLM 确认命中
    with patch("app.services.hub_issues.hub_dedup._confirm_via_llm", return_value=existing.id):
        dup = find_duplicate_hub(db_session, cur)
    assert dup == existing.id  # Operation 能查到同类型候选（不要求 linear_uuid）


def test_no_cross_type_merge(db_session: Session):
    # 已有 Bug_fix hub，当前是 Operation → 不应合并（type 约束）
    _hub(db_session, type_="Bug_fix", linear="lin-1", emb=[1.0, 0.0])
    cur = _hub(db_session, type_="Operation", linear=None, emb=[1.0, 0.0], title="x2")
    db_session.flush()
    dup = find_duplicate_hub(db_session, cur)
    assert dup is None  # 跨类型不合并
```

> 注：现有 find_duplicate_hub 的 LLM 确认是内联的（load_prompt + router.complete）。为可测，Task 1 顺带把 LLM 确认段抽成 `_confirm_via_llm(hub, candidates) -> int|None`（纯重构，行为不变），测试 mock 它。若不想抽，测试改为 mock LLMRouter。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_dedup.py -v`
Expected: FAIL（Operation 候选被 linear_uuid IS NOT NULL 过滤掉 / 无 _confirm_via_llm）

- [ ] **Step 3: 改候选过滤 + 抽 _confirm_via_llm**

`hub_dedup.py` 候选 select（约 :66-76）改为：
```python
    q = select(HubIssue).where(
        HubIssue.id != hub.id,
        HubIssue.deleted_at.is_(None),
        HubIssue.product_line_code == hub.product_line_code,
        HubIssue.type == hub.type,
        HubIssue.embedding.isnot(None),
    )
    if hub.type in ("Bug_fix", "Demand"):
        q = q.where(HubIssue.linear_uuid.isnot(None))
    rows = db.execute(q).scalars().all()
```
把 LLM 确认段（load_prompt→router.complete→_parse）抽成模块函数 `_confirm_via_llm(hub, top, router=None) -> int|None`，find_duplicate_hub 调它。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_dedup.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/hub_dedup.py backend/tests/unit/services/test_hub_dedup.py
git commit -m "feat(hub-dedup): 候选过滤按类型分流 + type 约束 + 抽 _confirm_via_llm"
```

---

### Task 2: 抽 maybe_supersede_duplicate 到 hub_dedup 公共

**Files:**
- Modify: `backend/app/services/hub_issues/hub_dedup.py`（加公共函数）
- Modify: `backend/app/services/hub_issues/linear_push.py`（删私有版，改 import）
- Test: `backend/tests/unit/services/test_hub_dedup.py`

**Interfaces:**
- Consumes: `find_duplicate_hub`（Task 1）
- Produces: `maybe_supersede_duplicate(db, hub) -> int|None`（供 creator + linear_push）

- [ ] **Step 1: 写失败测试**

```python
def test_maybe_supersede_marks_and_counts(db_session: Session):
    from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate
    existing = _hub(db_session, type_="Operation", linear=None, emb=[1.0, 0.0])
    cur = _hub(db_session, type_="Operation", linear=None, emb=[1.0, 0.0], title="dup")
    db_session.commit()
    with patch("app.services.hub_issues.hub_dedup.find_duplicate_hub", return_value=existing.id):
        dup = maybe_supersede_duplicate(db_session, cur)
    assert dup == existing.id
    db_session.refresh(cur); db_session.refresh(existing)
    assert cur.superseded_by_hub_issue_id == existing.id
    assert existing.occurrence_count == 2  # 1 + 1


def test_maybe_supersede_none_when_no_dup(db_session: Session):
    from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate
    cur = _hub(db_session, type_="Operation", emb=[1.0, 0.0]); db_session.commit()
    with patch("app.services.hub_issues.hub_dedup.find_duplicate_hub", return_value=None):
        assert maybe_supersede_duplicate(db_session, cur) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_dedup.py -k supersede -v`
Expected: FAIL（maybe_supersede_duplicate 未定义）

- [ ] **Step 3: 加公共函数 + 迁移 linear_push**

`hub_dedup.py` 加（照 linear_push._maybe_supersede_duplicate 搬）：
```python
from datetime import UTC, datetime
from app.repositories.status_history import StatusHistoryRepository


def maybe_supersede_duplicate(db: Session, hub: HubIssue) -> int | None:
    """查重命中则当前 hub supersede 到原 hub + 原 hub occurrence_count+1。
    返回原 hub_id，否则 None（含降级）。Commits。"""
    dup_id = find_duplicate_hub(db, hub)
    if dup_id is None:
        return None
    dup = db.get(HubIssue, dup_id)
    if dup is None or dup.deleted_at is not None:
        return None
    now = datetime.now(UTC)
    hub.superseded_by_hub_issue_id = dup_id
    hub.supersede_reason = f"hub-dedup: 与 {dup.short_code} 同一问题，合并复用"
    dup.occurrence_count += 1
    dup.last_seen_at = now
    StatusHistoryRepository(db).record(
        entity_type="hub_issue", entity_id=hub.id,
        from_status=hub.status, to_status=hub.status,
        changed_by="agent:hub_dedup", reason=hub.supersede_reason,
    )
    db.commit()
    logger.info("hub_superseded_by_dedup", hub_issue_id=hub.id, dup_hub_id=dup_id)
    return dup_id
```
`linear_push.py`：删 `_maybe_supersede_duplicate` 私有函数，改 `from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate`，调用点 `dup_id = maybe_supersede_duplicate(db, hub)`。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_dedup.py tests/unit/services/test_linear_push.py -v`
Expected: PASS（含 linear_push 既有测试不回归）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/hub_dedup.py backend/app/services/hub_issues/linear_push.py backend/tests/unit/services/test_hub_dedup.py
git commit -m "refactor(hub-dedup): 抽 maybe_supersede_duplicate 公共，linear_push 改调"
```

---

### Task 3: creator 毕业后调 dedup（所有类型）

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`
- Test: `backend/tests/unit/services/test_hub_issue_creator.py`（追加）

**Interfaces:**
- Consumes: `maybe_supersede_duplicate`（Task 2）、`get_settings`
- Produces: `ensure_hub_issue_for_ticket` 命中重复时 ticket 挂原 hub、返回 created=False

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_hub_issue_creator.py
from unittest.mock import patch


def test_graduate_merges_on_dedup_hit(db_session):
    # 已有 Operation hub（原 hub），造 ticket 毕业时命中它
    from app.models import HubIssue, Ticket, Source
    from app.services.hub_issues.creator import ensure_hub_issue_for_ticket
    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    orig = HubIssue(short_code="HUB-ORIG", type="Operation", title="开票失败",
                    status="created", product_line_code="发票云", occurrence_count=1)
    db_session.add(orig); db_session.flush()
    t = Ticket(short_code="TKT-D1", source_code="ksm", source_ticket_id="k-d1", type="Raw",
               status="received", title="开票失败", body="网络错误",
               predicted_type="Operation", product_line_code="发票云")
    db_session.add(t); db_session.commit()
    with patch("app.services.hub_issues.creator.maybe_supersede_duplicate", return_value=orig.id):
        res = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)
    assert res.created is False
    assert res.hub_issue_id == orig.id
    db_session.refresh(t)
    assert t.hub_issue_id == orig.id  # ticket 挂原 hub


def test_graduate_creates_when_no_dup(db_session):
    from app.models import Ticket, Source
    from app.services.hub_issues.creator import ensure_hub_issue_for_ticket
    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    t = Ticket(short_code="TKT-D2", source_code="ksm", source_ticket_id="k-d2", type="Raw",
               status="received", title="独立问题", body="xxx",
               predicted_type="Operation", product_line_code="发票云")
    db_session.add(t); db_session.commit()
    with patch("app.services.hub_issues.creator.maybe_supersede_duplicate", return_value=None):
        res = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)
    assert res.created is True
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator.py -k dedup -v`
Expected: FAIL（creator 未调 maybe_supersede_duplicate）

- [ ] **Step 3: creator 接入**

`creator.py`：
- import：`from app.config import get_settings` + `from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate`
- 在建 hub + `db.flush()` 后、最终 `db.commit()` 前插入：
```python
    if get_settings().hub_dedup_enabled:
        dup_id = maybe_supersede_duplicate(db, hub)
        if dup_id is not None:
            ticket.hub_issue_id = dup_id
            db.add(
                TicketHubIssueHistory(
                    ticket_id=ticket.id,
                    hub_issue_id=dup_id,
                    change_reason=f"hub-dedup 合并到 #{dup_id}（{created_by}）",
                    human_confirmed=created_by.startswith("user:"),
                )
            )
            db.commit()
            dup = db.get(HubIssue, dup_id)
            logger.info("hub_issue_dedup_merged", ticket_id=ticket.id, dup_hub_id=dup_id)
            return HubIssueResult(
                hub_issue_id=dup_id,
                hub_issue_short_code=dup.short_code if dup else "",
                ticket_id=ticket.id,
                type=dup.type if dup else issue_type,
                created=False,
            )
```
（放在现有建 hub 的 `db.add(hub); db.flush()` 之后、写 TicketHubIssueHistory/status_history/commit 之前——命中则提前 return，未命中走原逻辑）

> 注意事务：maybe_supersede_duplicate 内部 commit 了 hub（superseded）。命中分支再 commit ticket 挂靠。未命中分支走原有 commit。确认 hub.flush 后 maybe_supersede 能拿到 hub.id + embedding（find_duplicate_hub 内 ensure_hub_embedding 需要 hub 已 flush）。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator.py -v`
Expected: PASS（含既有 creator 测试不回归）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_issue_creator.py
git commit -m "feat(hub-dedup): creator 毕业后全类型查重，命中挂原 hub"
```

---

### Task 4: linear_push 已 superseded 跳过

**Files:**
- Modify: `backend/app/services/hub_issues/linear_push.py`
- Test: `backend/tests/unit/services/test_linear_push.py`（追加）

**Interfaces:**
- Produces: creator 已合并（superseded）的 hub，linear_push 不重复查/推

- [ ] **Step 1: 写失败测试**

```python
# 追加到 test_linear_push.py
def test_push_skips_already_superseded(db_session):
    from app.models import HubIssue
    from app.services.hub_issues.linear_push import push_hub_issue_to_linear
    hub = HubIssue(short_code="HUB-SUP", type="Bug_fix", title="x", status="created",
                   product_line_code="发票云", superseded_by_hub_issue_id=999)
    db_session.add(hub); db_session.commit()
    # 已 superseded → 直接 return，不调 Linear（无 key 也不报错）
    result = push_hub_issue_to_linear(hub.id)
    assert result is None
```

- [ ] **Step 2: 运行确认失败/通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_push.py -k superseded -v`
Expected: 先 FAIL（若无跳过逻辑走到别处）

- [ ] **Step 3: 加跳过判断**

`linear_push.py` 的 push 函数，在 `linear_uuid is not None` 跳过判断附近加：
```python
        if hub.superseded_by_hub_issue_id is not None:
            logger.info("linear_push_skip_superseded", hub_issue_id=hub_issue_id)
            return None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_push.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/linear_push.py backend/tests/unit/services/test_linear_push.py
git commit -m "feat(hub-dedup): linear_push 对已 superseded 的 hub 跳过"
```

---

### Task 5: 全量验证 + lint + 合并部署

- [ ] **Step 1: 全量单测**

Run: `cd backend && .venv/bin/pytest -m "not integration and not e2e and not eval" -q 2>&1 | tail -12`
Expected: 全绿（GLM flaky 忽略）

- [ ] **Step 2: lint**

Run: `cd backend && make lint 2>&1 | tail -8`
Expected: ruff + mypy 通过（新增如需 format 先 `ruff format`）

- [ ] **Step 3: 修复后提交**

```bash
git add -A && git commit -m "chore(hub-dedup): lint + format" || echo "无改动"
```

- [ ] **Step 4: 合并 main + push + SIT 部署**

```bash
git checkout main && git merge --no-ff feat/hub-dedup-all-types
git push origin main
ssh root@sit "cd /data/hub-issue && git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build"
ssh root@sit "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health"
```
Expected: health 200。hub_dedup_enabled 默认 true，Operation/Internal_task 毕业即生效查重（降级安全）。

---

## Self-Review

**Spec 覆盖**：改动1候选过滤→Task1；改动2抽公共→Task2；改动3 creator→Task3；改动4 linear_push跳过→Task4；测试→各Task+Task5。全覆盖。

**占位扫描**：无 TODO，每步完整代码。

**类型一致性**：`find_duplicate_hub(db, hub)→int|None`、`maybe_supersede_duplicate(db, hub)→int|None`、`_confirm_via_llm(hub, top)→int|None` 全程一致；HubIssueResult(hub_issue_id, hub_issue_short_code, ticket_id, type, created) 与现有构造一致。

**实现时校验点**：
1. `_confirm_via_llm` 抽取——现有 find_duplicate_hub 的 LLM 段是否好抽（Task1 Step3），若难抽则测试直接 mock LLMRouter.complete
2. creator 事务：maybe_supersede 内部 commit + 命中分支再 commit，确认无冲突（Task3 注）
3. find_duplicate_hub 内 ensure_hub_embedding 需 hub 已 flush 有 id（creator 里 db.flush() 已在前）
4. test_hub_dedup.py / test_hub_issue_creator.py 是否已存在（追加 vs 新建）
