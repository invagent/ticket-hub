# Architecture Decision Records

每阶段交付前在此新增 ADR，记录设计权衡。

| 编号 | 标题 | 阶段 | 状态 |
|------|------|------|------|
| 0001 | [技术栈与 Monorepo 结构](0001-stack-and-monorepo.md) | D0 | Accepted |
| 0002 | [INT PK 偏离 spec UUID](0002-int-pk-deviation.md) | D1 | Accepted |
| 0005 | [SLA 升级链](0005-sla-escalation-chain.md) | D2 | Accepted |
| 0012 | [admin 权限边界与飞书用户同步](0012-admin-permission-boundary.md) | D2 | Accepted |
| 0013 | [LLM Router 与多 Provider failover](0013-llm-router-provider-strategy.md) | D3 | Accepted |
| 0014 | [agent_decisions 极简审计表](0014-agent-decisions-minimal-audit.md) | D3 | Accepted |
| 0015 | [JSON 向量代替 pgvector](0015-json-vectors-over-pgvector.md) | D3-E | Accepted |

预留：
- 0007 状态级联与 sync_outbox fan-out (D4) — 已实现，ADR 待补（见 `services/cascade/`）
- 0008 KSM 反向操作的边界（决策 6）(D5)
- 0009 灰度切流策略 (5/25/50/100) (D6)
- PII 加密 / 主密钥轮换（季度）— 接海外 LLM 前补（D4 第③段已论证国内模型无新增暴露）
