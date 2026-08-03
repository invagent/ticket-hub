# 研发人员维度看板 设计文档

日期：2026-08-03
目标：在现有统计看板 /analytics 增加研发人员维度的两个图，帮助领导细致了解研发人员处理工单的情况（工单量 + 处理耗时）。

## 1. 背景

现有 /analytics 看板（工单维度统计）已上线。领导需要进一步看**研发人员**层面的数据——研发类工单（Bug_fix / Internal_task / Demand 三类，归属研发）在不同人员间的**处理总量**和**处理耗时**分布。

## 2. 已确认决策（brainstorm 结论）

| 决策点 | 结论 |
|---|---|
| 位置 | 并入现有 /analytics 页底部，新增「研发人员维度」区（不新建页） |
| 统计范围 | 仅 Bug_fix / Internal_task / Demand 三类研发工单（Operation/Complaint 不计入） |
| 人员维度 | `assigned_user_id`（处理人）——已验证这批数据研发工单的处理人即研发人员本人 |
| 工单量图 | 按研发人员 + 类型**堆叠柱状**（看某人主要做 Bug 还是需求） |
| 耗时图 | 每人**中位数 + 平均**耗时（中位数抗极端值，两者对比看长尾） |

## 3. 数据能力（已在 SIT 全量验证）

研发三类工单实际分布：Bug_fix 114 + Demand 339 + Internal_task 0 = **453 条**（这批发票云工单无内部任务；代码按三类写，未来有数据自动显示）。

- 442/453（97%）有 handle_hours，可算耗时
- 处理人分布真实有区分度且与研发责任人一致：汪意 42/魏文浩 37/梁瑞然 35/朱星宇 28…
- 耗时差异显著：汪意/魏文浩/梁瑞然/朱星宇 平均 200-260h（研发单周期长），王绪彪 50h/池宇 68h 明显短 — 对比有洞察价值

## 4. 后端设计

### 4.1 聚合方法（新增，复用现有 analytics.py）

`app/services/metrics/analytics.py` 现有 `compute_ticket_analytics` 返回 `TicketAnalytics`。新增一个字段 `by_dev_staff`，在同一次调用里算好（两个图共用一份数据，一个聚合足够）：

```
by_dev_staff: list[dict]  # 每个研发人员一项，按研发工单总数降序，top N（如 20）
  {
    "user_id": int | None,
    "name": str,                    # None → "(未分配)"
    "total": int,                   # 研发三类工单总数
    "by_type": {Bug_fix, Demand, Internal_task},  # 各类型数（堆叠图用）
    "median_handle_hours": float | None,
    "avg_handle_hours": float | None,
  }
```

实现要点：
- 过滤条件在现有 `_base_filter(flt)` 基础上叠加 `predicted_type IN (_DEV_TYPES)`，`_DEV_TYPES = ("Bug_fix","Internal_task","Demand")`
- 工单数 + by_type：一条 `group_by(assigned_user_id, name, predicted_type)` 查询，Python 侧聚合成每人 by_type + total（同现有 by_module 套路）
- 中位/平均：中位数用现有 `_percentile`（Python 侧，跨库一致，同 trend）；平均用 SQL `func.avg`。按 user 取 handle_hours 列表算中位（一条 `select(user_id, name, handle_hours)` 拉回 Python 分组）
- 未分配（assigned_user_id 为 NULL）单独成一项 name="(未分配)"，与现有 by_assignee 一致
- 排序 total 降序，top 20（研发人员通常不多，20 够）
- 时间/月份筛选：随现有 start/end 一起生效（研发看板也应支持月份筛选，复用同一 flt）

### 4.2 API

`GET /api/metrics/ticket-analytics` 响应加 `by_dev_staff: list[dict]`（TicketAnalyticsOut + compute 返回）。无新端点，无新参数。改后端后 `make gen-types`。

## 5. 前端设计

现有 `AnalyticsPage.tsx` 底部（耗时直方图之后）新增「研发人员维度」区，两个图并排或上下：

### 5.1 研发人员工单量（堆叠柱状）
- 横向柱状（BarChart layout="vertical"，Y=人名 top20，X=工单数），按类型堆叠（stackId），配色复用 TYPE_COLORS
- 数据源 `by_dev_staff[].by_type`
- tooltip 显示各类型数 + 合计
- 空数据（by_dev_staff 为空）显示「暂无研发工单」

### 5.2 研发人员处理耗时（分组柱状或表）
- 每人显示中位数 + 平均两个值。方案：横向分组柱状（每人两根：中位/平均，不同色），或简洁用**表格**（人名 / 工单数 / 中位耗时 / 平均耗时），表格对领导更易读且能同时看量+耗时
- **采用表格**：研发人员 top20，列=人名/研发工单数/中位耗时(h)/平均耗时(h)，按工单数降序；中位与平均并列，领导可一眼对比长尾（平均>>中位=该人有个别超长单）
- 耗时为空显示「—」

### 5.3 复用
- 类型配色/标签复用现有 TYPE_COLORS/TYPE_LABELS
- fmtHours 复用
- 月份筛选自动作用于研发区（同一 query）

## 6. 验证

- 后端：analytics 单测加 `by_dev_staff` 用例（构造 Bug/Demand 多人多单，断言某人 total/by_type/中位/平均）
- API：现有 test_metrics_analytics 加 `by_dev_staff` 字段存在断言
- 前端：AnalyticsPage.test 加研发区渲染断言（mock by_dev_staff，断言人名/工单数/耗时出现）
- SIT：看板底部研发区显示真实数据（汪意 42单、魏文浩 37单等；耗时中位 vs 平均对比），与 SQL 直查核对

## 7. 风险 / 说明

- **Internal_task 当前 0 条**：堆叠图/表里内部任务列恒为 0，属数据现实，非 bug；未来有内部任务自动显示
- **耗时口径**：仅计 handle_hours 非空的工单（442/453），与看板其他耗时指标一致
- **处理人 vs 研发责任人**：本设计用 assigned_user_id（已验证这批数据两者一致）。若未来真实工单出现"处理人≠研发责任人"（如客服转研发），需评估是否改用 CSV「研发责任人」字段——但那字段仅历史导入有，新工单无，故坚持 assigned_user_id 保持口径统一（同看板"只用系统原生字段"原则）
- 无新端点/新参数/新迁移，纯增量聚合 + 前端展示，改动小
