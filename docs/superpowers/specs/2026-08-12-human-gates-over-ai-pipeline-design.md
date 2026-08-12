# AI 全自动链路 + 可开关人工审核闸门（设计）

日期：2026-08-12
状态：待实现（brainstorm 已定，spec 待用户 review）

## 背景与核心理念

系统当前是 **AI 全自动**：入库 → AI 分类 → conf≥门槛自动毕业 → 自动分派/答复/推 Linear，主管**事后**修正。

现阶段 AI 识别成功率不够高，需要在自动链路的**每个关键动作前加一道人工审核闸门**。等 AI 准确率提升后，逐个关掉闸门，退回全自动。

**核心原则：保留 AI 全自动能力不动，只在关键节点插入「可开关的人工确认」。闸门开=人审核，闸门关=AI 直接自动执行（现状）。**

被分配的处理人是工单的中枢：他判断类型、确认答复、决定是否升级给产研。AI 从"自动执行"降级为"给建议"，但这个降级是**可逆的开关**，不是架构重写。

## 三道闸门总览

```
入库 → 分派引擎分处理人（不分类型，统一分）→ AI 跑分类（建议标签）
  → 【闸门①：分类确认】gate_classify_enabled
      开：所有工单毕业后停 pending_review，处理人确认/改判类型才继续
      关：conf≥门槛自动毕业直接分流（现状）
      ↓ 确认/自动后按类型分流
  ├─ Operation → 【闸门②：答复确认】operation_answer_accuracy_mode
  │     review：AI 起草答复转 reviewing 队列，人确认后发（✅ 已实现）
  │     off/observe：自动直发（现状）
  ├─ Bug_fix/Demand → 【闸门③：推 Linear 确认】gate_linear_push_enabled
  │     开：停 pending_linear_review，人确认 + 选模块负责人才推
  │     关：自动推 Linear（现状，用责任人路由）
  └─ Internal_task → 无外部动作
```

**三个独立开关**，可分别开关（用户明确要求：AI 准了逐个弱化）。

## 已确认的设计决策

| 决策点 | 选择 |
|--------|------|
| 入库分配 | 用刚上线的分派引擎分配处理人（所有类型统一分，写 handler） |
| 闸门①范围 | **全部类型**都过分类确认（不只研发类） |
| 闸门②（答复） | 复用已实现的 review 模式 |
| 闸门③（推 Linear） | 人确认 + 默认查模块负责人作 assignee + 可手动改选 |
| 开关粒度 | **三个独立开关**，各自可开可关 |
| 模块负责人来源 | 复用现有 `assignment_scopes_module` 表（产品线+模块→user_id） |
| 演进方向 | AI 准确率提升 → 逐个关闭闸门退回全自动 |

## 与现状的差距分析

| 闸门 | 现状 | 差距 |
|------|------|------|
| ① 分类确认 | `require_review_before_linear` 只拦研发类，进 pending_review + confirm-classification/reclassify 端点已有 | 扩展到**全部类型**；改开关语义 |
| ② 答复确认 | ✅ review 模式完整（2026-08-12 部署） | 无差距 |
| ③ 推 Linear 确认 | `linear_push_enabled` 总开关 + 研发类推责任人；owner-split 有责任人下拉 | 加「人确认 + 默认模块负责人 + 手动改选」的推送闸门 |

**关键：本次不推翻架构，是"扩展闸门①覆盖范围 + 新增闸门③的确认步骤 + 三开关独立化"。**

## 分派引擎的语义修正（重要）

前一个改动（研发类走分派引擎）让分派人**覆盖 `assigned_user_id`**。但在本模型下语义要重新理清：

- **处理人（handler）** = 分派引擎分配的人 = 工单中枢，负责判类型/确认答复/决定升级。写 `handler_user_id`（所有类型）。
- **责任人（assigned_user_id）** = 入库路由设的产品线/模块负责人，也是研发类推 Linear 的默认 assignee 来源之一。

**修正**：分派引擎写 `handler_user_id`（处理人），**不再覆盖 `assigned_user_id`**（责任人）。这样：
- 处理人由分派引擎决定（运营/客服/任何被分配的人）
- 推 Linear 的 assignee 默认取**模块负责人**（`assignment_scopes_module`），不是处理人也不是被分派人——解决了"分派人/责任人不在 Linear"的问题
- 处理人可在推送界面手动改选 assignee

> ⚠️ 这修正了 `2026-08-12-dev-class-dispatch-design.md` 里"派覆盖 assigned_user_id"的决策。原决策在"分派人即 Linear assignee"假设下成立，但本模型下 Linear assignee 改由模块负责人决定，故分派只写 handler。

## 架构与改动点

### 配置开关（`config.py`）

```python
# 三道人工审核闸门（AI 准确率提升后逐个关闭退回全自动）
gate_classify_enabled: bool = True       # 闸门①：全类型毕业后停 pending_review 待确认分类
                                         # （替代/泛化 require_review_before_linear）
# 闸门②：operation_answer_accuracy_mode = 'review'（已有，不新增）
gate_linear_push_enabled: bool = True    # 闸门③：研发类推 Linear 前停 pending_linear_review 待确认
```

`require_review_before_linear` 保留兼容或迁移为 `gate_classify_enabled`（实现时定，避免破坏现有 SIT env）。

### 闸门①：分类确认扩到全类型

**改 `creator.py` 的 auto 路径**（`create_hub_issue_for_ticket_auto`）：
- `gate_classify_enabled=True` → 所有毕业的 hub（不分类型）置 `pending_review`，不走任何自动分流（不答复、不推 Linear）
- `gate_classify_enabled=False` → 现状自动分流

**改 `confirm-classification` 端点**：确认后按类型分流——
- Operation → 进答复链（op_status=processing/agent，受闸门②控制）
- Bug_fix/Demand → 进闸门③（受 `gate_linear_push_enabled` 控制）
- Internal_task → 直接 created，无外部动作

**`reclassify` 端点**：已支持改判，扩展为改判后也按新类型分流（现有逻辑改判成 Operation 已回炉答复链，需补 Bug_fix/Demand 分支走闸门③）。

**分类确认队列**（`GET /pending-review`）：去掉"仅研发类"过滤，返回全部 pending_review。前端队列展示 AI 建议类型 + 置信度，处理人可确认或改判。

### 闸门③：推 Linear 确认 + 模块负责人

**新增 hub 状态 `pending_linear_review`**（或复用 pending_review 的后续子态）：研发类分类确认后，若 `gate_linear_push_enabled=True`，不直接推 Linear，而是停在待推送队列。

**新增/扩展端点**：
- `GET /pending-linear-review`：待推 Linear 的研发类队列，每条附**默认模块负责人**（查 `assignment_scopes_module` by 产品线+模块）+ 该负责人的 Linear 映射状态
- `POST /confirm-linear-push`：处理人确认推送，body 带可选 `assignee_user_id`（手动改选，默认用模块负责人）→ 写 hub 的推送 assignee → 调 `push_hub_issue_to_linear`

**改 `linear_push.py`**：assignee 来源优先级——确认时传入的 `assignee_user_id`（手动选）> 模块负责人（`assignment_scopes_module`）> 现有责任人回落。team 路由跟随最终 assignee 的 `linear_team_id`。

**模块负责人查询**：新增 helper `resolve_module_owner(db, product_line_code, module) -> User | None`，查 `assignment_scopes_module`。查不到 → 回落责任人 → 再回落默认 team 无 assignee。

### 闸门②：答复确认（已实现，仅确认开关独立）

`operation_answer_accuracy_mode=review` 已完整。确认它作为独立开关运作——闸门①确认成 Operation 后，若 mode=review 走答复审核队列，若 off/observe 自动答复。无代码改动。

## 前端

- **分类确认队列**（主管工作台）：扩展现有 pending_review 卡片，去掉类型过滤，全类型展示；确认/改判后按类型给不同提示
- **推 Linear 确认队列**（新）：研发类待推送卡片，显示默认模块负责人 + Linear 映射状态（在/不在 Linear）+ assignee 下拉可改选 + 确认推送按钮
- **答复确认队列**：已有 reviewing 队列，无改动
- 三个队列可以在主管工作台并列，或按闸门开关状态显隐

## 数据流（完整，三闸门全开）

```
webhook → ingest → 分派引擎写 handler_user_id → AI 分类（建议）
  → 自动毕业 hub（gate_classify_enabled=True）→ status=pending_review（不分流）
  → 【处理人在分类确认队列】确认/改判类型
      ├─ Operation → op_status=processing/agent
      │     → operation_auto_reply drain → AI 起草 → mode=review → reviewing 队列
      │     → 【处理人确认答复】→ author_reply 级联回写客户
      ├─ Bug_fix/Demand → gate_linear_push_enabled=True → status=pending_linear_review
      │     → 【处理人在推送队列】默认模块负责人/可改选 → confirm-linear-push
      │     → push_hub_issue_to_linear（assignee=模块负责人或手选）
      └─ Internal_task → status=created（无外部动作）
```

## 开关演进路径（用户目标）

| 阶段 | gate_classify | mode | gate_linear_push | 效果 |
|------|--------------|------|-----------------|------|
| 现在（AI 不准） | on | review | on | 三道全人工 |
| AI 分类准了 | **off** | review | on | 分类自动，答复+推送人工 |
| AI 答复也准了 | off | off/observe | on | 只推 Linear 人工 |
| 全准 | off | off | **off** | 全自动（回到现状架构） |

## 测试计划

单测：
1. 闸门①开：Operation/Bug_fix/Demand/Internal_task 四类型毕业都停 pending_review，不自动分流
2. 闸门①关：四类型按现状自动分流
3. confirm-classification 确认 Operation → 进答复链；确认 Bug_fix → 进闸门③；确认 Internal_task → created
4. reclassify 改判后按新类型分流
5. 闸门③开：研发类确认分类后停 pending_linear_review 不推
6. confirm-linear-push 用默认模块负责人 → push assignee 正确
7. confirm-linear-push 手动改选 assignee → 覆盖默认
8. resolve_module_owner：命中/未命中（回落责任人）/负责人不在 Linear（pending）
9. 分派引擎写 handler_user_id 不覆盖 assigned_user_id（修正前一改动）
10. 三开关独立性：任意组合行为正确

## 风险与部署

| 风险 | 缓解 |
|------|------|
| 修正"分派覆盖 assigned_user_id" 与前一改动冲突 | 前一改动刚部署未验证生产，本次修正回滚该覆盖，改写 handler；spec 明确标注 |
| 闸门①全开 → 所有工单堆 pending_review 待处理 | 这是预期（人在中枢）；需处理人有足够人力，或调门槛/关闸门 |
| `assignment_scopes_module` 数据不全 → 模块负责人查不到 | 回落责任人 → 回落默认 team；查不到不阻塞，给处理人手动选 |
| pending_review 现有语义只研发类，扩全类型可能影响现有队列消费 | 迁移时全量检查 pending_review 消费方 |

- 可能需要迁移（新状态 `pending_linear_review` 若加 CHECK 约束）；实现时定。
- 无 API 破坏性变更（新增端点 + 扩展现有）；需 `make gen-types`。
- SIT 部署：git 驱动，改开关走 .env + up -d。

## 待实现时细化的开放项

- `require_review_before_linear` → `gate_classify_enabled` 的迁移方式（保留别名兼容，还是直接替换 + 改 SIT env）
- `pending_linear_review` 用独立状态还是复用 pending + 标记
- 前端三队列的布局（并列 tab / 按开关显隐）
