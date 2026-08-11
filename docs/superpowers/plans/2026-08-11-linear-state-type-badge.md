# 研发打回状态识别（linear_state_type + 列表徽标）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 hub 工单任务表用 Linear 归一化的 `state_type == "canceled"` 稳妥识别「研发已打回」并醒目呈现，不受研发自定义列名影响。

**Architecture:** hub_issues 新增 `linear_state_type` 列（迁移 0031）；回同步轮询把已拉回的 `state.state_type` 落库（零新增 API 调用）；hub 列表 API schema 带出该字段；前端徽标改用 `state_type` 判断打回。`canceled` 维持现有「只镜像不级联」行为不变。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic（backend）；Vite + React + TypeScript（frontend）；pytest / vitest。

## Global Constraints

- 迁移编号紧接现有最新 0030 → **0031**；add column 回填 NULL，不设 CHECK（Linear 未来可能新增 type）。
- `state_type` 取值为 Linear 归一化类型：`backlog / unstarted / started / completed / canceled / triage`；命名与已有 `HubIssueLinearIssue.state_type`（`models.py:692`）一致。
- **不做**：打回说明、抓评论、Linear 入站接口、详情页打回卡片、新 hub.status。
- 改后端 API schema 必须运行 `make gen-types` 同步 `frontend/src/api/openapi.json` + `types.ts`，否则 CI `make check-types` 失败。
- 后端单测用 SQLite in-memory，默认 `pytest` 只跑 unit。命令在 `backend/` 目录下用 `.venv/bin/pytest`。
- `canceled` 现有行为不变：只镜像 `linear_status`/`linear_state_type`，**不**走 `apply_hub_status`，不级联工单，不入 outbox。
- 前端改动 SIT 部署需单独跑 `build-frontend.sh`。

---

### Task 1: 数据模型 + 迁移 0031

**Files:**
- Modify: `backend/app/models.py`（`HubIssue`，在 `linear_status`（`:551`）之后加字段）
- Create: `backend/migrations/versions/0031_hub_linear_state_type.py`

**Interfaces:**
- Produces: `HubIssue.linear_state_type: Mapped[str | None]`（供 Task 2 写入、Task 3 读出）

- [ ] **Step 1: 在 models.py 的 HubIssue 加字段**

在 `backend/app/models.py` 中 `HubIssue` 类里 `linear_status`（`:551`）与 `linear_status_synced_at`（`:552`）之间加：

```python
    linear_state_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- [ ] **Step 2: 写迁移 0031**

查当前 head：`cd backend && .venv/bin/alembic heads`（应为 0030）。创建 `backend/migrations/versions/0031_hub_linear_state_type.py`：

```python
"""hub_issues.linear_state_type — Linear 归一化状态类型（判断研发打回）

Revision ID: 0031
Revises: 0030
"""

from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hub_issues",
        sa.Column("linear_state_type", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hub_issues", "linear_state_type")
```

> 注意：确认 `0030_ticket_handler.py` 里的 `revision` 实际字符串值（可能是 `"0030"` 或带后缀），`down_revision` 必须与之精确匹配。用 `cd backend && .venv/bin/alembic heads` 核对。

- [ ] **Step 3: 应用迁移验证不报错**

Run: `cd backend && .venv/bin/alembic upgrade head`
Expected: 成功，无报错；`.venv/bin/alembic current` 显示 0031。

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/migrations/versions/0031_hub_linear_state_type.py
git commit -m "feat(model): HubIssue.linear_state_type + 迁移0031(判断研发打回)"
```

---

### Task 2: 回同步落 state_type

**Files:**
- Modify: `backend/app/services/hub_issues/linear_status_sync.py`（`sync_linear_statuses` 的 hub 循环，`:136-160`）
- Test: `backend/tests/unit/services/test_linear_status_sync.py`

**Interfaces:**
- Consumes: `HubIssue.linear_state_type`（Task 1）；`IssueState.state_type`（已存在，`adapters/linear/types.py`）
- Produces: 轮询后 `hub.linear_state_type` 等于 Linear 返回的 `state.state_type`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/unit/services/test_linear_status_sync.py` 加测试。先看文件里现有 fake client / hub fixture 的写法并复用（现有测试已构造 `IssueState` 和已推 hub）。新增两个用例：

```python
def test_sync_writes_linear_state_type_on_canceled(db_session):
    # 构造一个已推 Linear 的 hub（linear_uuid 非空），fake client 返回 canceled 状态
    hub = _make_pushed_hub(db_session, status="in_progress")
    client = _FakeLinearClient(states=[
        _issue_state(hub.linear_uuid, name="Canceled", type_="canceled"),
    ])
    sync_linear_statuses(db_session, client=client)
    db_session.refresh(hub)
    assert hub.linear_state_type == "canceled"
    assert hub.linear_status == "Canceled"
    # canceled 不级联：hub.status 不被改动
    assert hub.status == "in_progress"


def test_sync_writes_state_type_for_custom_cancel_column_name(db_session):
    # 研发自定义列名 "Won't Do" 但 state_type 仍是 canceled
    hub = _make_pushed_hub(db_session, status="in_progress")
    client = _FakeLinearClient(states=[
        _issue_state(hub.linear_uuid, name="Won't Do", type_="canceled"),
    ])
    sync_linear_statuses(db_session, client=client)
    db_session.refresh(hub)
    assert hub.linear_state_type == "canceled"
    assert hub.status == "in_progress"
```

> 用文件内已有的 helper 名替换 `_make_pushed_hub` / `_FakeLinearClient` / `_issue_state`。若无 helper，参照文件顶部现有测试的 hub 构造与 fake client 内联写法。

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_status_sync.py -k state_type -v`
Expected: FAIL —— `hub.linear_state_type` 为 None（尚未写入）。

- [ ] **Step 3: 实现写入**

在 `linear_status_sync.py` 的 hub 循环里，镜像 `linear_status` 的 if 块（`:142-145`）之后、`_CASCADE_MAP` 查询（`:147`）之前，加：

```python
        if hub.linear_state_type != state.state_type:
            hub.linear_state_type = state.state_type
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_status_sync.py -v`
Expected: PASS（新增用例 + 原有用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hub_issues/linear_status_sync.py backend/tests/unit/services/test_linear_status_sync.py
git commit -m "feat(linear-sync): 回同步落 linear_state_type(零新增API调用)"
```

---

### Task 3: API 返回 state_type + 类型同步

**Files:**
- Modify: `backend/app/api/hub_issues.py`（`HubIssueSummary`，`:40-78`）
- Modify（生成物）: `frontend/src/api/openapi.json` + `frontend/src/api/types.ts`

**Interfaces:**
- Consumes: `HubIssue.linear_state_type`（Task 1）
- Produces: hub 列表/详情响应 JSON 带 `linear_state_type: string | null`（供 Task 4 前端读取）

- [ ] **Step 1: 加响应字段**

在 `backend/app/api/hub_issues.py` 的 `HubIssueSummary` 里，`linear_status`（`:59`）之后加：

```python
    linear_state_type: str | None  # Linear 归一化类型（canceled=研发打回）
```

`HubIssueSummary` 用 `from_attributes=True` 从 ORM 映射，字段自动带出；`HubIssueDetail` 继承它，详情响应同样带出。无需改 endpoint 组装代码。

- [ ] **Step 2: 生成类型**

Run: `make gen-types`
Expected: `frontend/src/api/openapi.json` 和 `types.ts` 更新，`HubIssueSummary` 出现 `linear_state_type`。

- [ ] **Step 3: 验证 CI 门槛**

Run: `make check-types`
Expected: PASS（openapi.json 与后端同步）。

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/hub_issues.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(api): hub 列表返回 linear_state_type + gen-types"
```

---

### Task 4: 列表徽标改用 state_type 判定打回

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssuesListPage.tsx`（研发工程状态列徽标，`:452` 附近渲染 + `:540` 文案）
- Test: `frontend/src/pages/hub-issues/__tests__/HubIssuesListPage.test.tsx`（若不存在则 create；或就近放到既有 hubActions 测试同级目录）

**Interfaces:**
- Consumes: `h.linear_state_type`（Task 3，`string | null`）、`h.linear_status`（现有列名文本）

- [ ] **Step 1: 写失败测试**

先确认现有前端测试布局：`ls frontend/src/pages/hub-issues/__tests__/ 2>/dev/null`。加/建 `HubIssuesListPage.test.tsx`，渲染研发工程状态单元格逻辑（若徽标逻辑内联难测，抽一个纯函数 `devStateBadge(h): { label, kind }` 到组件文件顶部再单测该函数）：

```tsx
import { describe, it, expect } from "vitest";
import { devStateBadge } from "../HubIssuesListPage";

describe("devStateBadge", () => {
  it("state_type=canceled 判为已打回(红色)，无论列名文本", () => {
    expect(devStateBadge({ linear_state_type: "canceled", linear_status: "Won't Do" }).kind).toBe("canceled");
  });
  it("非 canceled 走原列名映射", () => {
    expect(devStateBadge({ linear_state_type: "started", linear_status: "In Progress" }).kind).not.toBe("canceled");
  });
  it("未推送(两者皆空)显示未推送", () => {
    expect(devStateBadge({ linear_state_type: null, linear_status: null }).label).toBe("未推送");
  });
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npm run test -- HubIssuesListPage`
Expected: FAIL —— `devStateBadge` 未导出/未定义。

- [ ] **Step 3: 抽出并实现 devStateBadge**

在 `HubIssuesListPage.tsx` 顶部（现有 `LINEAR_ST` 映射 `:57` 附近）加导出的纯函数，把 `:452`/`:540` 处内联徽标逻辑改为调用它：

```tsx
export function devStateBadge(
  h: { linear_state_type?: string | null; linear_status?: string | null },
): { label: string; kind: string } {
  // 优先用归一化 state_type 判打回，不受研发自定义列名影响
  if ((h.linear_state_type ?? "").toLowerCase() === "canceled") {
    return { label: h.linear_status || "已打回", kind: "canceled" };
  }
  if (!h.linear_status) return { label: "未推送", kind: "backlog" };
  const kind = (h.linear_status ?? "").toLowerCase();
  return { label: h.linear_status, kind: kind in LINEAR_ST ? kind : "backlog" };
}
```

渲染处（原 `:452` 取 `LINEAR_ST[...]`、`:540` 显示 `{h.linear_status ?? "未推送"}`）改为：

```tsx
const badge = devStateBadge(h);
const st = LINEAR_ST[badge.kind] ?? LINEAR_ST.backlog;
// ... 用 st 上色，用 badge.label 显示文案
```

- [ ] **Step 4: 运行验证通过 + 类型检查**

Run: `cd frontend && npm run test -- HubIssuesListPage && npm run type-check`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/hub-issues/HubIssuesListPage.tsx frontend/src/pages/hub-issues/__tests__/HubIssuesListPage.test.tsx
git commit -m "feat(hub-list): 研发工程状态列用 state_type 判定已打回"
```

---

### Task 5: 全栈验证

**Files:** 无（验证任务）

- [ ] **Step 1: 后端 lint + unit**

Run: `cd backend && make lint && make unit`
Expected: PASS，覆盖率 ≥70%。

- [ ] **Step 2: 前端 type-check + test + build**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿。

- [ ] **Step 3: 根目录类型门槛**

Run: `make check-types`
Expected: PASS。

- [ ] **Step 4: 无独立可提交产物 —— 若前述任务已各自 commit 则跳过**

## Self-Review

- **Spec 覆盖**：改动 1（模型）→ Task 1；改动 2（回同步）→ Task 2；改动 3（API）→ Task 3；改动 4（徽标）→ Task 4；测试 → Task 2/4 内含 + Task 5 汇总。全覆盖。
- **Placeholder 扫描**：无 TBD/TODO；测试与实现均给出实际代码；helper 名处已注明「用文件内现有 helper 替换」并给出兜底做法。
- **类型一致**：`linear_state_type` 全程 `str | None` / `string | null`；`devStateBadge` 返回 `{label, kind}` 在测试与实现中一致；迁移 revision `"0031"`/down `"0030"` 已注明需与 0030 文件实际值核对。
