# 研发打回状态识别：linear_state_type + 列表徽标

**日期**：2026-08-11
**范围**：最小一期 —— 只做 `linear_state_type` 落地 + hub 工单任务表列表徽标。
**分支建议**：`feat/linear-state-type`（独立 worktree，见 [[multi-window-worktree-isolation]] 约定）。

## 背景与动机

研发在 Linear 侧把一个 issue 挪到取消类状态（Linear `state_type = "canceled"`），
业务语义上等于「研发打回了这个工单」。当前回同步（`linear_status_sync.py`）对
`canceled` 只镜像 `linear_status`（列名文本，如 "Canceled"），**不动 hub.status**——
这是 ADR 级的有意设计：研发取消的出路（改判 / 退回补料 / 关单 / reopen）需要主管人工判断，
系统不替主管决策。

问题在于「识别」这一步不够稳：

1. `linear_status` 存的是 **Linear 列名的原始文本**。Linear 允许每个 team 自定义工作流
   状态名，同属 `canceled` 类型的列名可能是 "Canceled" / "Cancelled" / "Won't Do" /
   "Duplicate" 等。用列名文本匹配「是否打回」会漏判研发自定义列名。
2. 前端 hub 工单任务表（`HubIssuesListPage.tsx`）已有「研发工程状态」列并对
   `canceled` 上了红色徽标，但徽标色是按 `linear_status` **文本**映射的，同样受上述限制。

本期用 Linear 归一化的 `state_type` 替代列名文本作为「是否打回」的判断依据，
让列表能稳妥、醒目地呈现被研发打回的工单。**这一期只做识别 + 列表呈现**，
为后续「Linear 打回接口 + 打回说明 + 详情页打回卡片」打地基。

## 明确不做（留待后续）

- ❌ 打回说明字段、抓 Linear 评论（方案 1/2/3 全部搁置）
- ❌ 提供给 Linear 的入站打回接口（`/webhook/linear` 或专用打回接口）——
  后续由 Linear 主动调用推送打回说明，届时再建
- ❌ 详情页「研发打回」卡片
- ❌ 新的 hub.status（打回不引入新状态，`canceled` 仍只镜像不级联，维持现有 ADR 设计）
- ❌ 任何新增的 Linear API 调用

## 设计

### 改动 1 — 数据模型：hub 存 `state_type`

`HubIssue`（`backend/app/models.py`，`linear_status` 定义在 `:551`）新增：

```python
linear_state_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- 取值：Linear 归一化类型 `backlog / unstarted / started / completed / canceled / triage`
  （与已有的 `HubIssueLinearIssue.state_type`，`models.py:692`，命名与语义保持一致）。
- 迁移 **0031**（`backend/migrations/versions/`，紧接 0030），add column，回填 NULL，
  不设 CHECK 约束（Linear 未来可能新增 type，宽松存储）。
- 语义：**判断「研发是否打回」= `linear_state_type == "canceled"`**，不再依赖列名文本。

### 改动 2 — 回同步落 state_type

`backend/app/services/hub_issues/linear_status_sync.py`：`sync_linear_statuses` 内已从
`IssueState` 拿到 `state.state_type`（当前只用于查 `_CASCADE_MAP`）。在镜像 `linear_status`
的同一分支补一行写入 `linear_state_type`：

```python
if hub.linear_status != state.state_name:
    hub.linear_status = state.state_name
    hub.linear_status_synced_at = now
    report.linear_status_refreshed += 1
# 新增：始终镜像归一化 state_type（判断打回的稳妥依据，不受列名自定义影响）
if hub.linear_state_type != state.state_type:
    hub.linear_state_type = state.state_type
```

- `client.py` / `IssueState` **无需改**（`state_type` 早已在查询与类型里）。
- **零新增 API 调用**——只是把已经拉回来的字段落库。
- `canceled` 的现有行为不变：仍只镜像、不走 `apply_hub_status`、不级联、不入 outbox。

### 改动 3 — API 返回 state_type

hub 列表接口的响应 schema 增加 `linear_state_type: str | None` 字段返回前端。
改后运行 `make gen-types` 同步 `frontend/src/api/openapi.json` + `types.ts`
（否则 CI `make check-types` 失败）。

### 改动 4 — 列表徽标醒目化

`frontend/src/pages/hub-issues/HubIssuesListPage.tsx` 研发工程状态列：

- 判断打回：**优先用 `linear_state_type === "canceled"`**（不再靠列名文本匹配 "cancel"）。
- 命中打回：显示醒目的红色「已打回」徽标（复用现有 `LINEAR_ST.canceled` 红色系
  `{ bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" }`，`HubIssuesListPage.tsx:57`），
  徽标内文案沿用具体的 `linear_status` 列名（让主管看到研发用的确切状态名）。
- 非打回：维持现有按 `linear_status` 文本映射的徽标逻辑。
- 研发状态筛选器（`:813`）保持数据驱动，不改。

## 测试

- **后端单测**（`tests/unit/.../test_linear_status_sync.py` 或对应现有文件）：
  issue 转 `canceled` 后 `hub.linear_state_type == "canceled"` 正确落库；
  且 hub.status **未**被改动（维持不级联的现有行为）。
- **前端单测**：研发工程状态列在 `linear_state_type === "canceled"` 时走红色「已打回」
  分支；列名文本为自定义值（如 "Won't Do"）但 `state_type==canceled` 时仍判为打回。

## 影响面

- 1 个迁移（0031，add column 回填 NULL，向后兼容）
- `linear_status_sync.py` 增一处赋值
- hub 列表 API schema 加一字段 + `make gen-types`
- `HubIssuesListPage.tsx` 徽标判断改用 `state_type`
- 2 组测试
- **无新增 Linear API 调用，无状态机变更，无对客出站变更**

## 部署备注

- 迁移 0031 需 `alembic upgrade head`。
- 前端改动 SIT 需单独跑 `build-frontend.sh`（见 [[classify-tuning-and-review-gate]] 踩坑）。
- 回填字段为 NULL；已推 Linear 的 hub 会在下一轮 5min 轮询自动补上 `linear_state_type`。
