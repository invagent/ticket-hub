# 研发类工单走多维分派引擎（设计）

日期：2026-08-12
状态：待实现

## 背景与问题

主管观察到：**未确认分类的工单（及研发类工单）处理人直接是入库时路由设的「责任人」，没有按分派规则分给指定的人。**

根因（三层）：

1. **分派引擎写死只对 Operation 触发**。`creator.py` 毕业逻辑里是 `if issue_type == "Operation"` 才调 `dispatch_operation_handler`。研发类（Bug_fix / Demand）毕业时根本不进分派引擎，处理人停留在入库时 Router 设的 `assigned_user_id`（责任人）。
2. **研发类的处理人流向本来是另一套**：毕业 → 推 Linear（按责任人 `linear_team_id` 做 team 路由，assignee = 责任人）。没有「运营处理人」概念。
3. **`require_review_before_linear`（默认开）** 让研发类自动毕业后置 `pending_review` 待主管确认。但分派逻辑在 `ensure_hub_issue_for_ticket` 内、`_mark_pending_review` 之前，时序上分派**会执行**——真正没执行是因为第 1 层的 type 判断。

## 目标

研发类（Bug_fix / Demand）也走多维分派引擎，与 Operation **共用同一批规则和人池**；分派选出的人既作为 ticket-hub 处理人，又作为 Linear issue 的 assignee 推过去（覆盖入库责任人）。

## 已确认的设计决策

| 决策点 | 选择 |
|--------|------|
| 适用范围 | Bug_fix / Demand 也走分派，与 Operation 共用同一批 DispatchRule 和人池（不加 type 维度） |
| 分派时机 | 研发类**自动毕业时**（`ensure_hub_issue_for_ticket` 内，同 Operation 时点） |
| 字段统一 | 分派结果**直接覆盖 `assigned_user_id`（责任人）**；Linear 推送天然用它做 team 路由 + assignee，`linear_push.py` 不改 |
| 无结果降级 | 分派未命中规则且无兜底 → **转人工**：置 `pending` 状态（复用现有「Linear 推送待人工」队列），不自动推 Linear |

### 为何复用 `pending` 队列（而非独立队列）

- `pending` 语义就是「研发类该推 Linear 但推不了，待人工」——分派无结果（缺处理人所以推不了）正是其子情况。
- 主管处理动作一致：补齐处理人/责任人 → 重推。复用现成的琥珀色卡片 + 重推按钮，零新增 UI。
- status_history 的 reason 写明「分派无结果」即可区分来源，无需独立状态枚举/迁移/前端卡片。

## 架构与改动点

### 分派引擎本身：零改动

`app/services/dispatch/engine.py` 的 `dispatch_operation_handler` 逻辑 **type 无关**——只做规则匹配（来源/产品线/模块/SLA）+ 选人（count / ratio / 溢出 / 兜底）。研发类共用同一批规则，直接复用。

**重命名**：`dispatch_operation_handler` → `dispatch_handler`（函数名带 "operation" 已名不副实；纯重命名，不改逻辑）。更新 `__init__.py` 导出和调用点。

### 改动点 1：`creator.py` 分派触发块

现状（`ensure_hub_issue_for_ticket` 内，约 161 行）：

```python
if issue_type == "Operation":
    from app.services.dispatch import dispatch_operation_handler
    dr = dispatch_operation_handler(db, hub)
    if dr.user_id is not None:
        hub.op_handler_user_id = dr.user_id
        from app.services.hub_issues.op_status import set_hub_tickets_handler
        set_hub_tickets_handler(db, hub, dr.user_id)
```

改为分两类：

```python
dispatch_missed = False
if issue_type in ("Operation", "Bug_fix", "Demand"):
    from app.services.dispatch import dispatch_handler
    dr = dispatch_handler(db, hub)
    if dr.user_id is not None:
        from app.services.hub_issues.op_status import set_hub_tickets_handler
        if issue_type == "Operation":
            hub.op_handler_user_id = dr.user_id          # 运营处理人
        else:
            hub.assigned_user_id = dr.user_id            # 研发类：覆盖责任人 → Linear 用它
        set_hub_tickets_handler(db, hub, dr.user_id)     # 处理人流动到关联工单
    elif issue_type in ("Bug_fix", "Demand"):
        dispatch_missed = True   # 研发类分派无结果 → 后续转 pending 人工
```

`HubIssueResult` 加字段 `dispatch_missed: bool = False`，把信号传给 auto 路径。

**注意**：`dispatch_handler` 内部的 `_hub_source_code` 反查依赖 ticket 已挂 hub（`ticket.hub_issue_id = hub.id; db.flush()`），现状已满足（分派块在 flush 之后）。研发类保持同样时序。

### 改动点 2：`create_hub_issue_for_ticket_auto` 降级分流

现状（约 227 行）：

```python
if result.created and result.type in ("Bug_fix", "Demand"):
    if get_settings().require_review_before_linear:
        _mark_pending_review(result.hub_issue_id)
    else:
        push_hub_issue_to_linear(result.hub_issue_id)
```

改为优先处理分派无结果：

```python
if result.created and result.type in ("Bug_fix", "Demand"):
    if result.dispatch_missed:
        # 分派无结果 → 转人工，不进 pending_review、不自动推 Linear
        _mark_dispatch_pending(result.hub_issue_id)
    elif get_settings().require_review_before_linear:
        _mark_pending_review(result.hub_issue_id)
    else:
        push_hub_issue_to_linear(result.hub_issue_id)
```

新增 `_mark_dispatch_pending`：置 `status='pending'` + status_history（`changed_by='agent:dispatch'`, reason=「分派无匹配处理人，转人工补齐后重推 Linear」）。仿现有 `_mark_pending_review` / `linear_push._mark_pending` 写法。

### Linear 推送：零改动

`linear_push.py:141` 已用 `hub.assigned_user_id` 做 team 路由 + assignee。研发类分派后 `assigned_user_id` = 分派人，推送天然生效。分派人若在 Linear 查无此人，走现有 pending 降级（不受影响）。

## 语义澄清

- 研发类分派后：`assigned_user_id`（责任人）= 分派人 = Linear assignee，三者一致。
- 入库 Router 设的责任人被分派**覆盖**——正是「分派规则决定这单归谁」的预期。
- `dispatch_log` 记录研发类分派日志，与 Operation 混在一起（共用人池，符合预期）。
- 主管手动毕业（`created_by=user:*`）：仍走分派（人已选了 type，但归谁仍按规则）。若主管想指定人，可后续在详情页改责任人——本设计不拦。
- **手动毕业 vs 自动毕业的降级差异**：`_mark_dispatch_pending` 只在 auto 路径（`create_hub_issue_for_ticket_auto`）触发。主管手动毕业（`POST /supervisor/create-hub-issue`）不经该路径，即便分派无结果也不置 pending——主管是显式操作，`assigned_user_id` 保持入库责任人，主管自行决定后续推送。这符合「人工操作不被自动降级打断」的一贯原则。

## 数据流（研发类，自动路径）

```
ingest → triage 分类 Bug_fix/Demand（conf ≥ 门槛 + auto 开）
  → ensure_hub_issue_for_ticket:
      毕业建 hub → hub-dedup 查重 → ticket 挂 hub → flush
      → dispatch_handler(db, hub):
          命中 → assigned_user_id = 分派人 + 关联工单 handler 流动
          无结果 → dispatch_missed = True
  → create_hub_issue_for_ticket_auto:
      dispatch_missed        → _mark_dispatch_pending（status=pending，待人工重推）
      require_review_before_linear 开 → _mark_pending_review（待主管确认分类）
      否则                    → push_hub_issue_to_linear（assignee=分派人）
```

## 测试计划

单测（`tests/unit/services/`）：

1. **研发类命中分派 → 覆盖 assigned_user_id**：Bug_fix 毕业，规则命中，断言 `hub.assigned_user_id == 分派人`、关联工单 handler 流动、`dispatch_log` 有记录。
2. **研发类分派无结果 → dispatch_missed=True**：无匹配规则+无兜底，断言 `result.dispatch_missed is True`、`assigned_user_id` 保持入库责任人不变。
3. **auto 路径分派无结果 → pending**：`_mark_dispatch_pending` 后 `status='pending'`、有 status_history、不调 `push_hub_issue_to_linear`、不置 `pending_review`。
4. **auto 路径分派命中 + review 开 → pending_review**：命中不误入 pending。
5. **Operation 回归**：Operation 仍写 `op_handler_user_id`（不写 assigned_user_id），行为不变。
6. **Demand 同 Bug_fix**：参数化覆盖两个研发类型。
7. **linear_push 用分派人**：`assigned_user_id`=分派人时，push 请求 assignee/team 为分派人的 Linear 映射（复用现有 push 测试骨架）。
8. **重命名回归**：`dispatch_handler` 别名/导出可用，现有 Operation 分派单测全绿。

## 部署与灰度

- 无迁移（不加字段/表，`HubIssueResult.dispatch_missed` 是内存 dataclass 字段）。
- 分派引擎受现有规则配置驱动：**未配研发类可命中的规则时，所有研发类都 dispatch_missed → 全部转 pending**。⚠️ 上线前必须确认 SIT/生产已有能覆盖研发类的分派规则，否则会把研发工单全堆进 pending 人工队列。
- 建议上线顺序：先在 SIT 配好覆盖研发类的规则 → 部署 → 观察研发类是否正确分派 + 推 Linear → 再上生产。
- OpenAPI/types 无变化（不动 API schema）。

## 风险

| 风险 | 缓解 |
|------|------|
| 研发类规则未配 → 全转 pending 淹没主管 | 上线前核验规则覆盖；文档明确告警 |
| 分派覆盖责任人，入库 Router 结果丢失 | 这是预期行为（用户已确认）；status_history 留痕可追溯 |
| 共用人池：运营被分派研发单 / 反之 | 用户已确认共用同一批人；如需隔离，未来加 match_types 维度（本期不做） |
| dispatch_log 混计研发/运营 | 共用池语义下符合预期；看板若需分类型统计，另议 |
