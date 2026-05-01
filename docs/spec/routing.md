# 路由规格（决策 D20，v0.5.6 草案）

> services/routing/router.py 的输入 / 决策 / 输出契约。

## 输入

```python
RouteRequest(
    ticket_id: int,
    source_code: str,                # ksm / zhichi / zammad
    raw_module: str | None,          # 来自 source 的模块名
    raw_feature: str | None,         # 来自 source 的功能/产品/版本
    customer_id: int | None,
    product_line_code: str | None,
    classification_hints: dict,      # type_classify Agent 输出
)
```

## 决策算法

1. **module 优先匹配**：在 `assignment_scopes_module` 表查 (raw_module, product_line_code) 命中的 user_id 列表
2. **若 module 命中 1 人** → assign_to = user
3. **若 module 命中 多人** → 触发 Conflict Detect Agent（决策 D13），输出"是否拆分"决策
4. **若 module 未命中**，回落 **feature 兜底**：在 `assignment_scopes_feature` 查 (raw_feature, product_line_code)
5. **若 feature 仍未命中** → fallback 到 default_pool（由当值 supervisor 兜底）
6. 任何分支均写 `agent_decisions`（type=`route`），可被主管 revert

## 输出

```python
RouteDecision(
    ticket_id: int,
    decision: Literal["assigned", "split", "default_pool"],
    assigned_user_ids: list[int],    # 拆分时多个；default_pool 时为空
    matched_scope: Literal["module", "feature", "none"],
    matched_scope_id: int | None,
    rationale: str,                  # 给主管 / 审计的解释
    confidence: float,
)
```

## 验收（D1）

- 50 条历史 ticket 重放命中率 ≥ 90%
- default_pool 比例 < 10%

## 监控（D2 起）

- 自动分配命中率（≥ 95% 目标）
- module / feature / default_pool 三档分布
- 调整率（reverted）< 10%
