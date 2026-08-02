# 工单维度统计看板 设计文档

日期：2026-08-02
目标：给领导层提供工单维度的研发管理统计看板，辅助研发/问题处理优化决策。

## 1. 背景

工作台需要一个面向领导层的工单统计看板。当前 SIT 有 5888 条二季度历史工单（飞书导入），未来有真实 KSM/智齿 webhook 工单持续入库。看板需常驻、统计所有工单。

现有 metrics 基础设施（探查结论）：
- `app/services/metrics/workbench.py`：按时间范围聚合 + 每日快照做 trend 的模式（可参照）
- `app/services/metrics/materializer.py`：Celery beat 5min 物化到 `materialized_metrics`（key-value + slot_key）的套路（可复用机制）
- `materialized_metrics` 是扁平 JSON blob，**不支持多维下钻**——新看板的多维聚合需实时查询或扩展
- 前端**无图表库**，只有手写 SVG sparkline（`WorkbenchPage.tsx:263`）——需引入 recharts
- `/api/metrics/dashboard`（旧，前端未用）+ `/api/metrics/workbench`（新）并存，均 `require_user`

## 2. 已确认决策（brainstorm 结论）

| 决策点 | 结论 |
|---|---|
| 指标口径 | **只用系统原生字段**（tickets 原生列 + hub_issues + SLA），新旧工单可比，不用飞书专属字段 |
| 时长指标 | 飞书"工单耗用时间"**回填到系统原生列**：`actual_resolved_at` = received_at + 耗时；新增 `handle_hours` 数值列。未来真实工单走 SLA watcher 填同列 |
| 数据范围 | 常驻看板，统计所有 tickets（含未来入库） |
| 位置/权限 | 新建主管/管理员专属统计页（require_supervisor） |
| 图表库 | 引入 recharts |
| 卡片 | KPI 总览行 + 产品线×类型 + 处理人负载 + 月度趋势/耗时分布（全部四类） |

## 3. 数据能力（已在 SIT 全量验证）

| 指标 | 数据源 | 填充率 | 可用 |
|---|---|---|---|
| 工单类型 | predicted_type 列 | 100% | ✅ Operation5435/Demand339/Bug_fix114 |
| 状态 | status 列 | 100% | ✅ |
| 产品线/模块 | product_line_code/module 列 | 99%/96% | ✅ |
| 处理人 | assigned_user_id 列 | 81% | ✅ |
| 处理耗时 | 飞书耗时→handle_hours(回填) | 92% | ✅ 中位23h/均值44h/p90 87h |
| SLA 标准 | 飞书处理时长标准→sla_standard_hours(回填) | 99% | ✅ 40h/8h/4h |
| 创建时间 | received_at 列 | 100% | ✅ 2026-04~06 |

**明确不做**（数据全空或口径不符）：问题是否解决、满意度（飞书 0%）；产研状态（仅 7%）；飞书专属"整单超期状态"（改用系统 SLA 口径：handle_hours vs sla_standard_hours 算达成/超期）。

## 4. 架构

```
回填迁移(一次性) → tickets 新增 handle_hours / sla_standard_hours 列 + 回填历史5888条
                    ↓
后端 analytics 聚合服务(实时查询,多维)
  app/services/metrics/analytics.py: compute_ticket_analytics(db, filters)
                    ↓
API: GET /api/metrics/ticket-analytics (require_supervisor, 支持时间范围/产品线/类型筛选)
                    ↓
前端 /analytics 页(supervisor+) + recharts 图表组件
```

**为何实时查询而非物化**：多维下钻（类型×产品线×处理人×月份）组合爆炸，物化不划算；5888~万级量级实时聚合 SQL 足够快（加索引）。参照 workbench.py 的实时聚合模式，非 materialized_metrics 物化。

## 5. 后端设计

### 5.1 迁移 + 回填（一次性脚本）

`tickets` 新增两列：
- `handle_hours: Numeric | None` — 处理耗时（小时）
- `sla_standard_hours: Numeric | None` — SLA 处理时长标准（小时）

回填脚本 `scripts/backfill_handle_hours.py`：对飞书导入工单（source_payload._feishu_import 有值），读"工单耗用时间"→handle_hours、"处理时长标准"→sla_standard_hours；若 handle_hours 有值且 actual_resolved_at 空 → actual_resolved_at = received_at + handle_hours。幂等（已填跳过）。

未来真实工单：SLA watcher/creator 在解决时算 handle_hours（actual_resolved_at - received_at），保持同列口径。（本设计聚焦看板；watcher 改造列为后续 follow-up，看板对新工单 handle_hours 空时按"进行中"处理不计入耗时统计。）

### 5.2 聚合服务 `app/services/metrics/analytics.py`

```
compute_ticket_analytics(db, *, start=None, end=None, product_line=None) -> TicketAnalytics
```
返回结构（dataclass）：
- `kpi`: total / by_type{Operation,Bug_fix,Demand,Internal_task} / avg_handle_hours / sla_rate（handle_hours ≤ sla_standard_hours 占比）
- `by_product_line`: [{product_line, total, by_type{}, overdue_count}]（产品线×类型热力）
- `by_assignee`: [{user_id, name, total, avg_handle_hours}]（负载，top N）
- `trend`: [{month, total, median_handle_hours, p90_handle_hours}]（按月）
- `handle_hours_hist`: [{bucket, count}]（耗时区间直方图，如 0-4/4-8/8-24/24-72/72+）

全部纯聚合 SQL（GROUP BY + 分位数用 percentile_cont）。received_at 按北京时区切月（参照 workbench.py:71 range_window）。

### 5.3 API `app/api/metrics.py`

`GET /api/metrics/ticket-analytics?start=&end=&product_line=`（**require_supervisor**）→ `TicketAnalyticsOut`。
- 时间范围默认最近 3 个月；无参数返回全量
- 复用 metrics router，新增 schema

## 6. 前端设计

### 6.1 依赖 + 页面

- `npm install recharts`（前端首个图表库）
- 新页 `frontend/src/pages/analytics/AnalyticsPage.tsx`，路由 `/analytics`，Layout 导航按 supervisor+ 过滤（同现有反思诊断的角色 gate 模式）
- 顶部时间范围筛选（最近3月/本季度/自定义）+ 产品线筛选

### 6.2 图表卡片（recharts）

1. **KPI 总览行**：4 个 stat 卡片——总量、类型占比(饼图 PieChart)、平均处理耗时、SLA 达成率(带颜色阈值)
2. **产品线×类型**：堆叠柱状图(BarChart stacked)，X=产品线 top10，堆叠=类型；配超期数标注
3. **处理人负载**：横向柱状图(BarChart layout=vertical)，Y=处理人 top15，X=工单量；tooltip 显示平均耗时
4. **月度趋势 + 耗时分布**：
   - 折线图(LineChart)：4/5/6 月工单量 + 耗时中位数/p90 双 Y 轴
   - 直方图(BarChart)：耗时区间分布(0-4/4-8/8-24/24-72/72+ 小时)

图表配色遵循现有 AI 分类标签色系（Bug=红/需求=蓝/运营=黄/内部=灰，见 TicketDetailPage）。

### 6.3 类型同步

改后端 API 后运行 `make gen-types` 更新 types.ts。

## 7. 验证

- 后端：analytics 服务单测（构造多类型/多产品线/多月 tickets，断言聚合数值）；API 端点 require_supervisor 权限测试
- 回填脚本：dry-run 统计 + SIT 执行后核对 handle_hours 填充率 ~92%、actual_resolved_at 回填数
- 前端：AnalyticsPage 组件测试（mock API 数据渲染各图表）；type-check + build
- SIT 实测：看板加载显示 5888 条的真实分布（类型/产品线/处理人/耗时），与 SQL 直查核对一致

## 8. 风险

- **未来工单 handle_hours 口径**：本期只回填历史；真实工单的 handle_hours 填充依赖 SLA watcher/creator 改造（follow-up）。在此之前新工单 handle_hours 空，耗时/SLA 指标只反映历史——看板需标注"效率指标基于已完成工单"
- **recharts 引入**：前端首个图表库，bundle 体积增加（~100KB gzip），可接受
- **实时聚合性能**：万级 tickets 无压力；量级增长到十万级时再考虑物化（materialized_metrics 扩 slot_key）
- **assigned_user_id 19% 空**：处理人负载图会有"未分配"分组，属真实情况需展示
