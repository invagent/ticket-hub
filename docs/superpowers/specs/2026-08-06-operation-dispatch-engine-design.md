# Operation 运营处理人分派引擎 设计

日期：2026-08-06
状态：设计已确认，待写实施计划

## Context

当前 Operation 工单毕业后，若 agent 自动答复失败、需补料、被驳回或系统异常，一律「转人工」到模糊的"主管"（`resolve_supervisor_name()` 返回 default_pool 用户或固定字符串"主管"）。缺少「按规则把工单分派给指定运营处理人」的能力。

参考同目录邻居项目 `ticket-hub` 的 `t_dispatch_*` 派单规则引擎（该项目建了完整数据模型但运行时未接线），为本项目 Operation 工单引入一套**独立的多维规则引擎**，在毕业时预分配具体运营处理人。

关键背景（现状勘察）：
- 已有 `Router`（`app/services/routing/router.py`）按「产品线+模块 → `assignment_scopes_module` → 责任人」分配，但那是**研发责任田**，与运营处理人不是同一批人。本设计的 dispatch 与 routing **正交**。
- `hub_issues.op_handler` 是 `String(64)` 名字字符串（非 user_id）；drain 任务（`operation_answer.drain_operation_auto_reply`）靠 `op_handler=='agent'` 判「未处理」。
- 毕业逻辑在 `creator.ensure_hub_issue_for_ticket`，Operation 毕业时置 `op_status='processing'`、`op_handler='agent'`。
- 转人工消费点：`operation_answer.py`（transfer/C补料/D未过floor/exception）+ `ksm_ingester.py`（驳回）。

## 8 项关键决策（已与用户确认）

1. **触发时机**：所有 Operation 工单**毕业那一刻**就按规则预分配处理人（不等答复）。
2. **规则独立性**：运营处理人 ≠ 研发责任田，用**独立**规则表（不复用 `assignment_scopes`）。
3. **规则形态**：**多维规则引擎**（来源/SLA/产品线/模块多维匹配 + 多人负载均衡）。
4. **完整对齐邻居 `t_dispatch_*`**：规则表 + 分派人表（配额权重 + main/overflow 两层）+ 溢出规则 + 兜底配置，**真正接线**。
5. **计数状态**：独立 `dispatch_log` 派单留痕表，作为按数量/按比例的计数来源。
6. **按天重置**：按比例/按数量都**按当天**计（查 `dispatch_log.created_at >= 今日零点`，**今日零点 = 北京自然日 00:00 换算成 UTC**，与项目其他按天逻辑 `metrics/workbench.py` 的 BEIJING 时区口径一致），跨天靠时间窗口天然重置，**无定时任务**。
7. **处理人存储**：新增 `op_handler_user_id`（int FK→users）记准确运营，`op_handler` 名字字段同步；不动 `assigned_user_id` 的研发语义。
8. **与自动答复关系**：毕业时预分配运营写 `op_handler_user_id`，但 `op_handler` 名字**仍保持 `'agent'`**，让 drain 照常跑自动答复（agent 先试，运营兜底）。答复失败/转人工时才把 `op_handler` 名字切成该运营。

## 架构

新增服务 `app/services/dispatch/`（与 `routing/` 平行）。核心：

```python
dispatch_operation_handler(db, hub) -> DispatchResult(user_id, user_name, rule_id, tier, reason)
```

**触发点** — `creator.py` Operation 毕业处：
- 调 `dispatch_operation_handler` 算运营 → 写 `hub.op_handler_user_id`
- `op_handler` 名字仍 `"agent"`（不打断 drain 自动答复）
- 无命中 → `op_handler_user_id = None`

**消费点** — 封装 `resolve_op_handler(db, hub, settings)` 统一「预分配运营 > 主管」回落：
- `operation_answer.py` 所有转人工分支（transfer / C 补料 / D 未过 floor / exception）
- `ksm_ingester.py` 驳回分支
- 取代现有裸调 `resolve_supervisor_name()`；预分配运营若已停用则回落主管

**数据流**：
```
Operation 毕业 → dispatch 规则引擎选运营 → 写 op_handler_user_id(预分配)
              → op_handler="agent" 照常 → drain 跑自动答复
                  ├─ D 答复成功 → 关单(运营仅备案)
                  └─ 转人工/C/exception/驳回 → op_handler 切成预分配运营名 → 交接
```

## 数据模型（4 张新表）

命名遵循本项目约定：下划线表名、INT autoincrement PK、JSON 字段（PG JSONB / SQLite 兼容）。

### `dispatch_rules`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| name | String(128) | 规则名 |
| match_sources | JSON | 来源列表，空=不限 |
| match_product_lines | JSON | 产品线列表，空=不限 |
| match_modules | JSON | 模块列表，空=不限 |
| match_sla | JSON | SLA 等级列表，空=不限（**取值待定，先留空**）|
| dispatch_mode | String(16) | `count` / `ratio` |
| rule_type | String(16) | `primary` / `overflow` |
| overflow_rule_id | INT FK→self, null | count 模式满后指向的溢出规则 |
| priority | INT | 多规则命中取数字小者 |
| is_active | Bool | |

匹配语义：多个 match_* 之间 **AND**，每个列表内 **OR**，空列表=该维度不限。多条命中按 `priority` 取一条 primary。

### `dispatch_assignees`
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| rule_id | INT FK→dispatch_rules | |
| user_id | INT FK→users | 运营处理人 |
| alloc_value | Numeric | **ratio 模式**：相对权重（5:3:2，不必凑100）|
| daily_cap | INT, null | **count 模式**：当日分派上限，null=不限 |
| tier | String(8) | `main` / `overflow` |
| is_active | Bool | |

### `dispatch_config`（key-value 兜底）
| 字段 | 类型 | 说明 |
|------|------|------|
| key | String(64) PK | 如 `default_operation_assignee` |
| value | String(128) | 兜底运营 user_id |

### `dispatch_log`（留痕 + 计数源）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| hub_issue_id | INT FK | |
| rule_id | INT FK, null | 兜底时 null |
| assignee_user_id | INT FK | |
| tier_hit | String(16) | `main`/`overflow`/`default` |
| created_at | DateTime(tz) | 按天计数依据（`>= 今日零点`）|

一条新迁移建这四张表（当前 head 0027，实际取未占用的下一个可用号）。

## 分派算法

### count 模式（按数量）
```
1. 匹配 dispatch_rules（AND/OR + priority 取一条 primary）
2. 取该规则 tier=main 且 is_active 的 assignees
3. 查每人今日（created_at >= 今日00:00 AND rule_id=X）已分派数
4. 过滤掉已达 daily_cap 的人 → 剩下选今日最少者
5. main 全部达上限：
     - 有 overflow_rule_id → 对溢出规则 assignees 重复 3-4
     - 无 → 回落 dispatch_config 兜底运营
6. 仍无 → op_handler_user_id=None（转人工回落主管）
7. 写 dispatch_log(tier_hit=main/overflow/default)
```
`daily_cap` 是 count 模式核心；`alloc_value` 不用。**溢出触发条件 = main 层全部达 daily_cap**。

### ratio 模式（按比例）
- 按 `alloc_value` 归一化算应得占比（5:3:2 → 50%/30%/20%）
- 查今日各人已分数，算「实际占比 vs 应得占比」，选**缺口最大**者
- **不设溢出**（比例本身即均摊，无"满"概念），`overflow_rule_id` 留 null，`daily_cap` 留 null
- 跨天靠只查当天计数天然重置

### 字段用法对照
| 字段 | count | ratio |
|------|-------|-------|
| daily_cap | ✅ 每人当日上限 | ➖ null |
| alloc_value | ➖ 默认 | ✅ 相对权重 |
| overflow_rule_id | ✅ 满了溢出 | ➖ null |

## 前端管理界面

放 `/admin` 分工配置域下（与 `assignment_scopes` 研发责任田界面并列），参考 `frontend/src/pages/admin/scopes/` 多标签页结构。

- **规则列表**：规则名 / 匹配摘要 / 模式 / 优先级 / 溢出关联 / 启用开关
- **规则编辑弹窗**：匹配条件多选（产品线/模块/来源/SLA）、模式切换（切换动态显示 daily_cap 或 alloc_value）、溢出规则下拉（仅 count）、优先级、规则名
- **分派人子表**：动态行，运营下拉（复用 `UserSelect`）+ 视模式显示 daily_cap/alloc_value + tier
- **派单日志查看**：读 dispatch_log，看某规则今日各人已派数（验证配比/配额）

**后端 admin 端点** `app/api/admin_dispatch.py`（`require_admin`，与现有分工配置一致）：
- `GET/POST/PUT/DELETE /api/admin/dispatch/rules`
- `GET/POST/PUT/DELETE /api/admin/dispatch/rules/{id}/assignees`
- `GET/PUT /api/admin/dispatch/config`
- `GET /api/admin/dispatch/logs?rule_id=&date=`

前端类型走 `make gen-types` 从 OpenAPI 生成。

## 错误处理与边界

原则：**绝不阻断毕业，绝不阻断自动答复**。
- 分派计算异常 → 吞异常记 warning，`op_handler_user_id=None`，毕业照常
- assignee 停用/删除 → 分派时过滤；全组不可用走溢出/兜底
- **并发不加锁**：两单几乎同时毕业可能都派同一人（±1 单倾斜），按天粒度可接受 —— **已知可接受偏差**
- 改配置不追溯当天已派记录，次日自然生效
- 预分配运营在转人工时已停用 → `resolve_op_handler` 校验有效性，失效回落主管
- hub 无 product/module → 匹配不中 → 兜底/主管

## 测试策略

单测为主（SQLite in-memory）。
- **分派引擎**（`tests/unit/services/test_dispatch.py`）：AND/OR 匹配、空列表不限、priority；count 今日最少/daily_cap 满/溢出/兜底；ratio 权重缺口最大/跨天重置；边界（无命中、停用过滤、异常吞掉）；dispatch_log 留痕
- **集成点**：creator 写 op_handler_user_id 且 op_handler 仍 'agent'、研发类不受影响；operation_answer 各转人工分支用 resolve_op_handler；drain 扫描口径 op_handler=='agent' 仍成立；ksm_ingester 驳回转预分配运营
- **admin 端点**（`tests/unit/api/test_admin_dispatch.py`）：CRUD、权限 403、校验
- **前端**：vitest（mode 切换、分派人动态行）
- **验证**：`make unit` + `make lint` + `make gen-types` + `make check-types`

## 不改动
- 不动 `routing/`（研发责任田）语义
- 不动 `assigned_user_id`
- 不改 drain 扫描口径（`op_handler=='agent'`）
- 研发类 hub（Bug_fix/Demand/Internal_task）不参与运营分派（`op_handler_user_id` 恒 None）
