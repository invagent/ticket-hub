# ADR-0002: 主键采用 INT autoincrement 偏离 spec 的 UUID

- 状态：Accepted (D1)
- 日期：2026-05-06
- 决策依据：upgrade_plan.md §4 spec vs D0 已落地 schema

## 背景

`upgrade_plan.md §4` 全部 30+ 表的 PK 设计为 `uuid PRIMARY KEY DEFAULT gen_random_uuid()`。
D0 阶段已落地的 sources / product_lines / users 三张表则使用 `INT PRIMARY KEY AUTOINCREMENT`。

D1 引入 14 张新表时面临选择：

- **A：D1 用 UUID + 与 D0 INT 表混用**（PK 类型不一致，FK 类型混乱）
- **B：迁移 D0 改 UUID + D1 用 UUID**（纯净，但 D0 已上线则破坏性更新）
- **C：D1 也用 INT，全仓库 INT 一致**（偿离 spec，简单一致）

## 决策

**采用 C：所有表使用 `INT autoincrement` PK。**

## 理由

1. **D0 已落地一致性**：D0 三张表已用 INT，混合 UUID 会让 FK 类型混乱
2. **测试成本**：UUID 需要 `pgcrypto` 扩展或应用层 `uuid.uuid4()` 注入；SQLite 测试环境无原生 UUID 类型，需要走 `Uuid` 复合类型（多一层封装），降低测试速度
3. **D1 验收门槛与 PK 类型无关**：50 条历史 ticket 重放命中率 ≥ 90% 这件事不依赖 PK 形式
4. **未来可换**：PK 改 UUID 是单次 schema migration，等真到上线压测、可枚举攻击成实际威胁时再切换；现在切代价大于收益

## 后果

- 所有 ORM 模型 `id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)`
- FK 全部用 `INT`
- `customer_identities.source_user_id` / `tickets.source_ticket_id` 等业务标识仍用 `text`（这些是源系统外部 ID，与 PK 类型无关）
- 序列 ID 在生产环境暴露顺序信息（可枚举工单总数）—— D6 上线前若仍敏感，再做一次 PK 迁移
- spec `gen_random_uuid()` 默认值改为 SQLAlchemy 应用层 autoincrement
- `created_by_agent_run_id` 等审计字段（spec 用 UUID FK 到 agent_runs.id）—— 等 D3 引入 agent_runs 表时一并 INT
