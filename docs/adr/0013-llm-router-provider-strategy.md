# ADR-0013: LLM Router 与多 Provider failover 策略

- 状态：Accepted (D3)
- 日期：2026-06-11
- 决策依据：D3 Agent 全家桶接入 LLM；`app/core/llm_router/`

## 背景

D3 起所有 Agent（classify / conflict_detect / dedup / escalation_classify）需要调
LLM。面临的问题：

- 单一 Provider 不可靠（网络抖动、限流、临时不可用）会让整条 ingest 链失败
- 不同 Provider/模型成本与准确率差异大（评测见 `docs/eval/2026-06-11-classify-v1-baseline.md`：
  deepseek-v4-flash 90% vs glm-4-flash 73%）
- 未来可能接 OpenAI/Anthropic（海外），但接入前必须先补 PII 脱敏

## 决策

**抽象 `LLMRouter`，按 `LLM_PROVIDER_ORDER` 顺序遍历 Provider，可重试错误自动 failover；
不可重试错误（鉴权/业务）立即抛出。**

- Provider 实现 `BaseLLMProvider`（~80 行/个），当前 GLM(智谱) + DashScope(deepseek/qwen)
- `from_settings(only=...)` 支持单 Provider 锁定，供评测 A/B
- 错误分级：`ProviderRetryableError`（网络/5xx/429）→ 下一个 Provider；
  `ProviderError`（鉴权/400）→ 立即 `LLMRouterError`，不浪费配额重试
- 结构化日志 `llm_router_call_ok/retry/failed`（provider/model/agent/latency/tokens/cost）
  即审计，不另建 agent_runs 表（见 ADR-0014）
- embeddings/vision 各自走独立 httpx 客户端但沿用同一 `LLM_PROVIDER_ORDER` 与 failover 思路

## 理由

1. **可用性**：单 Provider 故障不拖垮 ingest；retryable failover 透明
2. **成本/质量可调**：换模型只改 env（`LLM_PROVIDER_ORDER` / `*_MODEL`），代码不动
3. **评测友好**：`only=` 锁定单 Provider 跑离线评测，免改配置
4. **快重试 vs 快失败**：区分 retryable/non-retryable，鉴权错不无谓重试
5. **PII 边界内建**：海外 Provider 不进 `LLM_PROVIDER_ORDER` 默认值，直到 PII encryptor 就位

## 后果

- 新增 Provider = 实现接口 + 注册，无 API 变更
- 国内管理大模型（GLM/DashScope/qwen-vl）同一供应商边界，无 PII 新增暴露（见
  D4 第③段设计），故 Vision 多模态未阻塞于 PII encryptor
- `agent_runs` 表（spec 设计的 LLM 调用流水）由结构化日志覆盖，暂不建表
- 代价：日志非结构化查询（要成本报表时，D5 末再考虑物化 agent_runs）
