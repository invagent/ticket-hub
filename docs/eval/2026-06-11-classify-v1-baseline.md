# classify_v1 评测基线 — 2026-06-11

> 数据集：`backend/tests/eval/dataset_v1.jsonl` @ 60 条（50 真实 KSM 手标 + 10 合成；16 条 `needs_review`）
> 跑分器：`scripts/eval/run_eval.py`（`--provider` 单 Provider 对比）
> Prompt：`backend/prompts/classify_v1.md`（v1，未调优）
> 门槛：D3 验收 ≥ 90%

## 三方对比

| 指标 | glm-4-flash | glm-4.5-flash | **deepseek-v4-flash** |
|------|------------:|--------------:|----------------------:|
| 整体准确率 | 0.733 | 0.800 | **0.850** |
| 已确认标签准确率¹ | 0.841 | 0.864 | **0.955** |
| Operation recall | 0.407 | 0.630 | **0.741** |
| Bug_fix recall | 1.000 | — | 0.960 |
| 平均置信度 | 0.873 | 0.910 | 0.869 |
| 60 条总成本 | $0（免费档） | $0.0066 | $0.0062 |
| 耗时 | 219s | 533s | **175s** |

¹ 剔除 16 条 `needs_review` 歧义样本后的准确率（更接近模型真实水平）。

注：表中数字基于 `sample-001` 标签修正**前**的跑分。修正后（该条三模型均判对）推得
deepseek-v4-flash 为整体 **0.867** / 已确认 **0.977**。

## 结论与决策

1. **deepseek-v4-flash 设为默认主 Provider**（`LLM_PROVIDER_ORDER=dashscope,glm`），GLM 作 fallback。
   单工单成本 ~$0.0001，远低于 $0.05 目标。
2. **已确认标签口径 95.5% 已过 90% 门槛**；整体口径 85% 未过，但 9 个误判中 7 个落在
   `needs_review` 歧义样本——主要瓶颈是**标签噪声而非模型能力**。
3. 共性误判模式（三个模型一致）：
   - 「能否/是否支持 X」类咨询 → 误判 Demand（ksm-013/024）
   - 带报错提示的操作咨询 → 误判 Bug_fix（ksm-045/047）
   - 这两类正是 prompt v2 调优方向（补边界规则 + few-shot）。
4. `sample-001`（页面卡顿）原 D0 标签 Operation 与 prompt 定义冲突（prompt 明确「UI 卡顿」属
   Bug_fix），三个模型也一致判 Bug_fix——已修正标签。

## 下一步

- [ ] 人工复核 16 条 `needs_review` 标签（误判分析时 7 条模型判得可能比标签更合理）
- [ ] classify prompt v2：补「能力咨询 vs 需求」「报错提示 vs 操作咨询」边界规则
- [ ] 复核+v2 后重跑，目标整体口径 ≥ 90%
- [ ] 数据集扩到 100 条（升级计划 D3 目标），补 conflict / dedup 标注

## 复现

```bash
cd backend
.venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl --provider dashscope
.venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl --provider glm
GLM_MODEL=glm-4-flash .venv/bin/python ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl --provider glm
```
