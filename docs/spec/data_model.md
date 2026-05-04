# Data Model 规格（v0.5.6）

> 状态：D0 评审 — `tickets.type` 决策已落地（2026-05-02），其余 3 项待评审项延后到 D1 评审会议。
> 基线来自 `upgrade_plan.md` §4（v0.5.6, ~30+ 表）。
> D0 仅落地下表；其余表按阶段递进上线（见 §阶段表）。

## 阶段上线总览

| 阶段 | 新增表 |
|------|--------|
| D0 | sources / product_lines / users |
| D0+ | ksm_issue_type_mappings（轻量类型映射，替代 xlsx 迁移） |
| D1 | user_supervisors / user_partners / assignment_scopes_module / assignment_scopes_feature / assignment_scope_history / customers / customer_identities / customer_merge_history / tickets / hub_issues / hub_issue_relations / ticket_hub_issue_history / status_history / hub_issue_reply_history |
| D2 | notification_log（D6 决策） |
| D3 | pii_maps / agent_runs / agent_decisions / agent_decision_targets |
| D4 | attachments / knowledge_chunks / sync_outbox |
| D5 | （无新表，扩 hub_issues 内部任务字段） |
| D6 | （无新表，删除老表） |

## D0 表定义

### sources

源系统注册表（ksm / zhichi / zammad / linear）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | int | PK | 自增 |
| code | varchar(32) | UNIQUE NOT NULL | 系统短代码：ksm / zhichi / zammad |
| name | varchar(128) | NOT NULL | 中文名 |
| is_active | bool | DEFAULT true | 是否启用 |
| created_at / updated_at | timestamptz | server_default now() | 时间戳 |

### product_lines

产品线（不隔离工单流，决策 D2）。

### users

飞书 SSO 唯一身份，软删；多源 ID 列（employee_no / ksm_account / zhichi_agent_id）（决策 D19）。

详细字段见 `migrations/versions/0001_d0_initial.py` 与 `app/models.py`。

## 已决策项（D0 评审通过 — 2026-05-02）

### tickets 三类型判别 — 单表 + `type` 列

**决策**：tickets 用单表 + `type` enum 列区分 (`raw` / `parent` / `child`)，**不**分三表。

- 索引：`(type, status, created_at)` 复合索引覆盖三类视图查询
- 约束：`type='child'` 时 `parent_id IS NOT NULL`；`type='parent'/'raw'` 时 `parent_id IS NULL`（CHECK constraint）
- 取舍：JOIN 路径单一（拆单 / 跨源关联都是同一张表自联），优于三表 UNION
- 决策范围：仅 `tickets`；`hub_issues` 4 类型仍按 §待评审 评

### KSM 问题类型映射 — 轻量 PG 表（替代 xlsx 迁移）

**决策**：xlsx 迁移取消（决策 R2 撤销），改为人工建少量 KSM 问题类型映射表。

#### `ksm_issue_type_mappings`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | int | PK | 自增 |
| ksm_category | varchar(64) | NOT NULL | KSM 原始问题分类（如「财务-应付审核」） |
| ksm_subcategory | varchar(64) | NULL | 子分类（可空） |
| product_line_code | varchar(64) | FK → product_lines.code | 映射到的产品线 |
| target_module | varchar(128) | NOT NULL | 目标模块名（路由用，匹配 assignment_scopes_module） |
| target_feature | varchar(128) | NULL | 目标功能名（路由 fallback，匹配 assignment_scopes_feature） |
| classification_hint | varchar(32) | NULL | 类型提示：operation / bug_fix / demand / internal_task |
| notes | text | NULL | 运营备注 |
| is_active | bool | DEFAULT true | |
| created_at / updated_at | timestamptz | | |

- UNIQUE: `(ksm_category, ksm_subcategory)` 防重复
- 维护：`/admin/ksm-mappings` 前端 CRUD（D1 一并交付）
- 初始数据来源：人工根据 KSM 实际分类录入；不再从 xlsx 自动迁移
- 评测集 / 路由 fallback 时，可作为 router 的补充提示

> 同步存在一份 yaml 占位 `backend/config/mappings/ksm_issue_types.yaml`，用于 D0~D1 早期联调（未连 PG 时使用）；D1 评审决定 yaml 是否退役、改为 PG 单一来源。

## 待评审项（延后到 D1 评审会议）

- hub_issues 4 类型（Operation / Bug_fix / Demand / Internal_task）的稀疏列方案 A 是否仍合适
- customer_identities 软删与 merged_into_customer_id 的级联关系
- agent_decisions 仅 `executed`/`reverted` 两态（决策 D18）
