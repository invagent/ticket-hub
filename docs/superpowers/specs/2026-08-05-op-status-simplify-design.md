# Operation 状态机简化：补料转主管线下处理 + 删除 resupplied

**日期**：2026-08-05
**范围**：Operation `op_status` 专属状态机（`hub_issues.op_status`）
**类型**：状态机简化 + 补料流程改造

## 背景

当前 Operation 工单有 6 个 `op_status` 状态：`processing / answered / closed / supplementing / resupplied / exception`。

现状「需补料」走的是**打回客户**的闭环：

```
agent 判需补料 → supplementing（打回客户，request_supply → KSM supplyKsmOrder）
             → 客户补料重推同 billId → resupplied
             → drain_operation_auto_reply 自动重扫重答
```

这条链路把补料责任推给客户、依赖客户重推来推进状态机，且引入了 `resupplied` 这个纯粹为「触发重答」而存在的中间态。业务上希望改成：**补料由兜底主管线下联系客户收集，主管拿到资料后在系统里人工写答复**，不再打回客户、不再依赖客户重推。

## 目标

1. 删除 `resupplied` 状态，Operation 状态机从 6 个精简到 5 个。
2. 重定义 `supplementing` 语义：从「已打回客户，等客户补料」→「兜底主管正在收集补料」。
3. 人工写答复（`POST /reply`）推进状态机到 `answered`，补上现有的语义空洞（当前 `/reply` 只改 `reply_content` 不动 `op_status`，与 agent 答复不对称）。
4. 保留驳回计数逻辑（answered 单同一单再进 → `reject_count += 1`）。

## 非目标

- 不动 hub 底层状态机（`hub.status`）和 ticket 层状态。
- 不删除「打回客户要补料」的手动能力——`POST /request-supply` 端点、`supply_sync`、outbox `supply` kind 全部保留，仅 agent 不再自动触发。
- 不改 T+7 自动关单、驳回、exception 恢复等既有逻辑（除 resupplied 相关分支外）。

## 最终状态机（5 态）

保留：`processing / supplementing / answered / closed / exception`

```
   [毕业] processing ──────┬─ agent 自动答复成功 ──→ answered ── T+7 未驳回 ─→ closed
      op_handler=agent      │  (op_handler=agent)          │
                            │                              │ 客户驳回同 billId
                            │                              │ (reject_count+1)
                            │                              ↓
                            ├─ agent 判需补料 ──→ supplementing        processing
                            │  op_handler=兜底主管   （主管收集补料）    (op_handler=兜底主管)
                            │  ⚠️ 不再打回客户            │
                            │                            │ 主管线下拿到资料
                            │                            │ 人工 POST /reply
                            │                            ↓
                            ├─ agent 转人工 ─→ processing  answered ←── 人工写答复推进
                            │  (op_handler=兜底主管)       (op_handler=user:主管)
                            │
                            └─ replay 系统故障 ─→ exception（兜底主管，走 /re-answer 恢复）
```

## 详细设计

### 1. 状态常量与约束

**`app/services/hub_issues/op_status.py`**
- 删除 `OP_RESUPPLIED = "resupplied"` 常量。
- `_VALID` 集合移除 `OP_RESUPPLIED`。

**`app/models.py`（第 501-503 行）**
- `ck_hub_issues_op_status` CheckConstraint 去掉 `'resupplied'`：
  ```
  op_status IS NULL OR op_status IN
  ('processing','answered','closed','supplementing','exception')
  ```

**新迁移 `0026_op_status_drop_resupplied.py`**（down_revision=`0025_outbox_hub_issue_nullable`）
- `upgrade()`：drop 旧约束 → create 新约束（不含 resupplied）。
- **不迁移数据**（用户确认生产库无 resupplied 存量）。
- `downgrade()`：drop 新约束 → create 含 resupplied 的旧约束。

### 2. agent 判需补料：转主管，不打回客户

**`app/services/agents/operation_answer.py`（branch C，第 253-267 行）**

现状：
```python
if route.branch == "C":
    note = (route.supply_note or "").strip()
    if not note:
        return _transfer("需补料但 supply_note 为空，降级留主管")
    request_supply(db, hub.id, note=note, requested_by="agent:ai_cs")  # ← 打回客户
    apply_op_status(db, hub, to_status=OP_SUPPLEMENTING, handler="agent", reason="需补料")
    ...
```

改为：
- **删除 `request_supply(...)` 调用**（agent 不再自动打回客户）。
- `apply_op_status` 的 `handler` 从 `"agent"` 改为 `resolve_supervisor_name(db, settings)`（兜底主管），`reason` 改为如「需补料，转主管线下收集」。
- supply_note 仍写入审计（`_record_decision(branch="C", ..., supply_note=note)`），供主管参考「缺什么资料」；note 为空仍走 `_transfer`。
- 移除该文件对 `request_supply` / `SupplySyncError` 的 import（若无其他引用）。

**drain 扫描口径（第 307-320 行）**
- 移除 `HubIssue.op_status == OP_RESUPPLIED` 分支。扫描仅保留「刚毕业未处理」：`op_status=processing 且 op_handler='agent'`。
- supplementing 态由兜底主管处理，`op_handler != 'agent'`，天然不被 drain 扫到——符合「不抢主管正在处理的单」原则。
- 移除 `OP_RESUPPLIED` import，更新文档串（第 288-292 行）。

### 3. 人工写答复推进状态机

**`app/api/hub_issues.py` — `POST /{hub_issue_id}/reply`（第 199-225 行）**

现状：`author_reply()` 只写 `reply_content` + 级联，不改 `op_status`。

改为：`author_reply()` 成功后追加 `apply_op_status(db, hub, to_status=OP_ANSWERED, handler=f"user:{user.name}", reason="主管人工答复")`。

设计取舍：
- op_status 变更放在**端点层**，不放进 `author_reply` 服务。理由：`author_reply` 是通用回复服务，agent 答复路径（`operation_answer.py`）已自己调 `apply_op_status(→answered)`；若把状态变更塞进 `author_reply`，会与 agent 路径重复设置。端点层设置边界清晰——「主管手动 reply」这一入口负责推进状态机。
- `author_reply` 内部 `db.commit()`；`apply_op_status` 不 commit（设计如此）。因此需在 `apply_op_status` 后再 `db.commit()` 一次。需重新 `db.get(HubIssue)` 拿到 hub 对象（`author_reply` 未返回 ORM 对象）。
- **幂等/边界**：仅当 hub 是 Operation 时推进（`author_reply` 已强制 Operation-only，非 Operation 会抛 `ReplySyncError`，端点不会走到状态变更）。若 op_status 已是 answered，`apply_op_status` 幂等返回 False，无副作用。

### 4. supplementing 态客户主动重推同 billId

**`app/services/ingest/ksm_ingester.py`（第 87-97 行）**

现状：`op == OP_SUPPLEMENTING` 时 `apply_content_refresh` + 转 `resupplied`。

改为：`op == OP_SUPPLEMENTING` 时仅 `apply_content_refresh`（刷新内容供主管参考），**op_status 保持 supplementing 不变**，不再转 resupplied。
- 移除 `apply_op_status(→OP_RESUPPLIED)` 调用与 `OP_RESUPPLIED` import。
- 日志保留（如 `ksm_ingest_supplement_refresh`），仍返回 `_dedup_result(existing)`（不重跑 triage）。

**驳回分支（`op == OP_ANSWERED`，第 98-116 行）保持不变**：`reject_count += 1` + 转 `processing` + `op_handler=兜底主管`。此逻辑已符合需求。

### 5. 前端

**`frontend/src/components/OpStatusBadge.tsx`（第 12 行）**
- 删除 `resupplied` 徽章定义。
- `supplementing` 徽章文案建议从「补充资料/等客户」改为「补料中（主管收集）」之类，与新语义一致（具体文案实现时定）。
- 检查工单列表 / hub_issue 详情页 op_status 筛选下拉，若硬编码含 resupplied 选项则一并移除。

## 影响面清单

| 文件 | 改动 |
|---|---|
| `app/services/hub_issues/op_status.py` | 删 OP_RESUPPLIED 常量 + _VALID |
| `app/models.py` | CheckConstraint 去 resupplied |
| `migrations/versions/0026_*.py` | 新迁移改约束（不迁数据） |
| `app/services/agents/operation_answer.py` | branch C 去 request_supply + handler 改主管；drain 去 resupplied 分支 |
| `app/services/ingest/ksm_ingester.py` | supplementing 重推只刷内容不转态 |
| `app/api/hub_issues.py` | /reply 端点追加 apply_op_status(→answered) |
| `frontend/src/components/OpStatusBadge.tsx` | 删 resupplied 徽章 + supplementing 文案 |
| `app/services/ksm/writeback.py`（第 241 行注释） | 更新过时注释「由 auto_answer request_supply 入队时置」——agent 不再自动 request_supply |

**保留不动的手动补料入口**（确认非目标）：`POST /request-supply`（`hub_issues.py:251`）、`batch_request_supply`（主管勾选工单列表批量打回，`supervisor.py:358`）。agent 自动 supply（`operation_answer.py:260`）删除后，该文件对 `request_supply` / `SupplySyncError` 的 import（第 22 行）变为死引用，一并移除。

## 测试

- **单测**：
  - agent 判需补料 → op_status=supplementing 且 op_handler=兜底主管，且**不**产生 supply outbox 行。
  - supplementing 态 KSM 重推同 billId → 内容刷新，op_status 仍 supplementing。
  - answered 态 KSM 重推同 billId → reject_count+1 + 转 processing（回归测试，确认未破坏）。
  - `POST /reply` 成功 → op_status=answered，op_handler=user:xxx，reply_content 已写。
  - drain 扫描不再捞 supplementing / 已删的 resupplied 态。
  - CheckConstraint 拒绝写入 resupplied（迁移后）。
- **回归**：T+7 自动关（`close_overdue_answered`）、exception → /re-answer 恢复链路不受影响。
- 手动 `POST /request-supply` 打回客户仍可用（保留能力）。

## 迁移与上线

1. `git pull` + `alembic upgrade head`（0026）。
2. 重启后端 systemd。
3. 前端 build + 部署。
4. 无数据回填（生产确认无 resupplied 存量）。
5. 回滚：`alembic downgrade -1` 恢复含 resupplied 的约束。

## 待确认的实现细节（非阻塞）

- supplementing 徽章的中文文案最终用词（实现时定，不影响逻辑）。
- `POST /reply` 追加状态变更后，是否需要在响应体回传新的 op_status（当前 `AuthorReplyResponse` 不含 op_status；建议加上便于前端刷新，实现时定）。
