# Data Model 规格（v0.5.6 草案）

> 此文档是 D0 起草版，待评审。基线来自 `upgrade_plan.md` §4（v0.5.6, ~30+ 表）。
> D0 仅落地下表；其余表按阶段递进上线（见 §阶段表）。

## 阶段上线总览

| 阶段 | 新增表 |
|------|--------|
| D0 | sources / product_lines / users |
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

## 待评审项（D1 评审会议）

- tickets 表三类型（Raw / Parent / Child）的判别字段：`type` 列 vs 三表分库
- hub_issues 4 类型（Operation / Bug_fix / Demand / Internal_task）的稀疏列方案 A 是否仍合适
- customer_identities 软删与 merged_into_customer_id 的级联关系
- agent_decisions 仅 `executed`/`reverted` 两态（决策 D18）
