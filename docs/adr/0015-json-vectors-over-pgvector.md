# ADR-0015: JSON 列存向量 + Python 余弦，代替 pgvector

- 状态：Accepted (D3-E)
- 日期：2026-06-12
- 决策依据：D3-E dedup `migrations/0009_d3_e_ticket_embeddings.py`；`services/agents/dedup.py`

## 背景

D3-E dedup（跨源重复工单判定）需要向量召回：把工单 embedding 后按余弦相似度找候选，
再送 LLM 判定。spec 与 docker-compose 都预置了 `pgvector` 扩展。

但接入 pgvector 有成本：扩展依赖、迁移要 `CREATE EXTENSION`、SQLite 测试环境无对应
类型（要 mock 或跳过）、ANN 索引调参。而当前工单量级是**百级/天**。

## 决策

**向量存普通 `JSON` 列（`ticket_embeddings.vector`），召回用 Python 余弦暴力扫最近
`DEDUP_CANDIDATE_POOL`(200) 条。不引 pgvector。**

- `ticket_embeddings`：PK=ticket_id，`vector` JSON、`model`、`dim`
- 召回：按 id 倒序取最近 200 条（排除自身/已删/Child）→ Python 算余弦 → 阈值+topK
- 同款思路后续可复用于 How-To/知识向量（若自建；D4 第③段已改为接飞书知识库，暂不需要）

## 理由

1. **量级匹配**：百级/天，200 条 × 千维余弦在 Python 里毫秒级，无需 ANN
2. **零新基建**：JSON 列 PG/SQLite 都原生支持，迁移无扩展依赖，测试不用 mock 向量库
3. **跨库测试一致**：单测 SQLite 直接跑真实召回逻辑，不降级
4. **可演进**：迁 pgvector 是一次加列 + 换召回实现，召回接口（recall_candidates）已隔离
5. **决策对称**：与 ADR-0014「不过早建表」、ADR-0013「日志代流水」同一克制原则

## 后果

- `recall_candidates()` 是唯一向量召回入口，迁移点单一
- 召回是 O(pool × dim) 全扫——**写明的代价**：pool 调大或量级上千/天后进 profile 热点，
  届时迁 pgvector + ANN 索引（CLAUDE.md 技术债与 plan 已标注冻结条件）
- embedding 维度变更（换模型）时旧向量与新向量 dim 不一致 → 余弦返回 0（已在
  `cosine_similarity` 处理 dim mismatch，不报错）
- 不依赖 `CREATE EXTENSION` 权限，部署到任意 PG 实例无障碍
