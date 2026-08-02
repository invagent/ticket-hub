# SIT 清理 mock + 飞书二季度工单导入 设计文档

日期：2026-08-02
分支：待定（建议 `feat/feishu-q2-import`）
目标环境：SIT（43.139.250.182，库 `ticket_hub_sit`）

## 1. 背景与目标

SIT 库当前是测试期 mock 数据（29 条工单、13 hub_issues、6 escalation 等），需要：
1. 清理掉所有 mock 业务流水数据，保留系统基础配置
2. 把飞书多维表格导出的 2026 二季度（4/5/6 月）真实工单导入 SIT

数据来源：`docs/` 下三个 CSV（发票云工单列表 4/5/6 月），共 **5888 条工单**，100 列。
- 4月 1994 条 / 5月 1765 条 / 6月 2129 条

**关键约束**：代码库里**没有飞书工单数据源**（ingester 只有 KSM/智齿/zammad/escalation）。本次是一次性数据导入脚本，不新增数据源、不走 webhook、不改产品代码。

## 2. 已确认的决策（brainstorm 结论）

| 决策点 | 结论 |
|---|---|
| 写入方式 | **直接建库行**，不跑 AI 处理链（triage/路由/毕业都不跑） |
| 工单类型 | 用 CSV `提单类型` 映射：需求→Demand、BUG→Bug_fix、其余（含空）→Operation |
| source_code | **保留原始来源**：CSV `工单来源` KSM/智齿/多维表格（内部）分别映射 |
| 状态映射 | 按状态映射表（见 §5），不统一置 received |
| 清理边界 | 清业务流水，保留 users/skill_prompts/分工配置/system_settings/sources/产品线目录 |
| 处理人 | 按中文名匹配 SIT users（实测 99% 覆盖，4797/4804），匹配不上留空 |

## 3. 阶段一：清理 mock 数据

### 3.1 清理范围（业务流水，TRUNCATE/DELETE）

按外键依赖顺序清理（先子后父）：

**派生/历史表**（先清）：
- `agent_decisions`、`status_history`、`ticket_hub_issue_history`
- `hub_issue_reply_history`、`hub_issue_linear_issues`
- `sync_outbox`、`attachments`、`ticket_embeddings`
- `hub_issue_reply`（若存在）

**主业务表**：
- `tickets`（先解开 hub_issue_id 自引用）
- `hub_issues`
- `customers`、`customer_identities`（工单带出的客户身份）

### 3.2 保留（绝不清）

- `users`（99 用户，含已建的 Linear 映射 linear_user_id/linear_team_id）
- `skill_prompts` + `skill_prompt_history`
- `assignment_scopes_module` / `assignment_scopes_feature`
- `system_settings`（default_pool_user_id 等）
- `sources`（数据源种子）
- `product_lines` / `modules`（产品线/模块目录）
- `alembic_version`（迁移版本）

### 3.3 执行方式

单个事务内 `DELETE`（不用 TRUNCATE，避免 FK 级联意外）。执行前：
- **全库 pg_dump 备份**到 SIT 本地 + 记录时间戳
- 打印各表清理前后行数供核对

## 4. 阶段二：导入脚本

### 4.1 位置与形态

`backend/scripts/import_feishu_q2.py`——一次性导入脚本（不是产品代码，放 scripts/）。
- 读三个 CSV（`csv.DictReader`，`utf-8-sig` 处理 BOM）
- 幂等：以 `source_ticket_id`（工单ID）为去重键，已存在则跳过（支持重跑）
- 干跑模式 `--dry-run`：只解析统计不写库
- 分批 commit（每 500 条），失败可续跑

### 4.2 字段映射（CSV → tickets）

| tickets 字段 | CSV 列 | 处理 |
|---|---|---|
| `short_code` | — | `TKT-{n:06d}`，n = 现有 max 序号 + 递增 |
| `source_code` | `工单来源` | KSM→`ksm`、智齿→`zhichi`、多维表格（内部）→`feishu`（新种子）、空→`feishu` |
| `source_ticket_id` | `工单ID` | 如 FPY2026032300292；去重键 |
| `type` | — | 固定 `Raw` |
| `title` | `主题` | 截断 512 |
| `body` | `问题描述` + `工单处理过程` | 拼接 |
| `product_line_code` | `产品线` | catalog upsert（同入库逻辑，无则建） |
| `module` | `产品模块` | 直接存字符串 |
| `status` | `工单状态` | 见 §5 映射表 |
| `source_status` | `工单状态` | 原样存（保留 KSM 原状态文本） |
| `predicted_type` | `提单类型` | 需求→Demand、BUG→Bug_fix、其余→Operation |
| `predicted_confidence` | — | NULL（非 AI 分类，不编造置信度） |
| `assigned_user_id` | `处理人 (人员 )` | 按中文名匹配 users.id，匹配不上留 NULL |
| `reporter` | 反馈人/手机/邮箱/电话 | JSON |
| `received_at` | `工单创建时间` | 解析 `2026/04/01 13:22`，北京时区 |
| `created_at` | `工单创建时间` | 同上 |
| `source_payload` | 全行 | 存原始 CSV 行 dict（`{"_feishu_import": {...}}`）留档可追溯 |

**约束合规**：`type='Raw'` → 要求 `source_code IS NOT NULL AND source_ticket_id IS NOT NULL AND internal_split_id IS NULL`，映射满足。`predicted_type` ∈ 四类合法值。

### 4.3 处理人匹配

- 精确中文名匹配 `users.name`（实测覆盖 99%）
- 重名（如"陈少斌"两个、"杨慧莉""李志坚"各两个）：优先取有邮箱的 user_id
- 匹配不上的（吴伟 4 条、陈远丹 3 条）+ 处理人为空（1084 条）→ `assigned_user_id=NULL`
- **不落兜底**（导入是历史数据归档，不触发路由分配）

### 4.4 不做的事（明确排除）

- 不跑 triage/classify（predicted_type 直接从提单类型来）
- 不跑 Router（不分配、不落兜底）
- 不毕业 hub_issue（导入的是 ticket 层历史，不建 hub_issues）
- 不推 Linear、不入 sync_outbox
- 不写 agent_decisions（非 AI 决策）
- 不建 customer_identities（reporter 存 JSON 即可，避免污染身份图谱）

## 5. 状态映射表

| CSV 工单状态 | 数量 | → tickets.status |
|---|---:|---|
| 处理完成 | 4205 | `done` |
| 退回KSM处理 | 973 | `done` |
| 已退回 | 389 | `done` |
| 处理中 | 94 | `in_progress` |
| 升级产研处理 | 91 | `in_progress` |
| 处理关闭 | 60 | `closed` |
| 待处理 | 2 | `received` |
| （空）| 74 | `received` |

原始状态文本同时存入 `source_status` 保留完整信息。

## 6. 验证

导入后核对：
- 总行数 = 5888 − 重复 source_ticket_id（应约 5888）
- 按 source_code 分布：ksm ~5105、zhichi ~616、feishu ~167
- 按 predicted_type 分布：Demand ~339、Bug_fix ~114、Operation ~5435
- 按 status 分布符合 §5
- assigned_user_id 非空率 ~81%（4797/5888）
- 抽查 5 条：short_code 唯一、received_at 时间正确、reporter JSON 完整
- 前端工单列表页能正常翻页显示这批工单

## 7. 风险与回滚

- **回滚**：阶段一执行前的 pg_dump 是唯一回滚点；导入失败可按 source_ticket_id 批量删除重来
- **产品线目录膨胀**：CSV 产品线/模块会 upsert 出新目录行，属预期（真实产品线）
- **short_code 连续性**：清库后从 TKT-000001 重新开始（已确认用 TKT-递增，非 FPY 号）；原飞书工单 ID 保留在 source_ticket_id 可查
- **时区**：CSV 时间无时区，按北京时间（+08:00）解析入库
