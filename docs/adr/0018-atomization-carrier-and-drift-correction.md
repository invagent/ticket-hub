# ADR 0018：原子化载体归位 + ADR-0016 偏离纠正

- 状态：**提案（待采纳）** — 2026-09-09
- 相关：supersedes/追认 [[0016-agent-pipeline-restructure]] 的 §2.1 / §2.2 / §3；依赖 [[0017-unified-stage-single-owner-embedded-ticket]] 的 stage 基线
- 现状基线：`docs/spec/state-machines.md`（13 张实测状态机 + §15 偏离盘点）

---

## 1. 为什么要有这份 ADR

`docs/adr/` 从 **0016（2026-07-05）直接跳到 0017（2026-09-09）**，中间两个月发生四次架构级转向，**零 ADR**，只留在 commit message 与 plan 文档里。结果是打开 ADR-0016 对比代码会觉得"面目全非"——**问题主要在记录断档，不在代码腐化**。

本 ADR 做两件事：① 追认该保留的转向；② 把该退回的退回。

### 1.1 一处必须纠正的归因

初次盘点时判断「Child 拆分会破坏 KSM/智齿的一单对一单契约，所以 2026-09-08 改 Hub 子任务是对的」。**该判断经代码验证为错**：

- `ck_tickets_type_fields` 强制 `type='Child' → source_code IS NULL AND source_ticket_id IS NULL`
- 全部 5 个 outbox 生产者（`reply_sync` ×2、`status_cascade`、`supply_sync` ×2、`owner_split`）均按 `if t.source_code and t.source_ticket_id` 过滤

→ **Child 工单在架构上不可能产生任何对外回写**，对外只从 Parent 出。一单对一单契约在 ADR-0016 原设计里本就成立。

### 1.2 Hub 子任务转向的真实驱动

`fa71123` 提交说明自述：第 1 条「移除 `tri.is_mixed` 停摆拦截」、第 5 条「backfill 脚本支持**存量卡单**的一键补齐毕业」。

根因链：`split_auto_enabled` 默认 `False` → 混合单既不自动拆也不毕业，停摆进主管「拆单提案」队列 → 队列无人消化 → 混合单积压 → 改架构绕开。

**这是运营流程问题，不是设计缺陷**。可选解法本有三个（打开自动拆分开关 / 把拆单队列做进工作台 / 改架构），当时选了成本最高的第三个。

---

## 2. 待决：原子化载体

一条混合工单（「登录失败(bug) + 补开发票(operation)」）拆出的原子问题，**挂在哪一层**？

### 方案 A · ticket 层 Child 工单（ADR-0016 原设计，agent 自动拆）

```
Raw ticket ──triage(is_mixed)──> agent 自动 split
   └─ Parent (status=split, 持 source_code, 唯一对外出口)
        ├─ Child#1 (无 source_code) → 毕业 hub#1 → Bug_fix → 推研发
        └─ Child#2 (无 source_code) → 毕业 hub#2 → Operation → 答复
```

| 优点 | 缺点 |
|---|---|
| 原子单是**完整 ticket**，天然拥有独立 type/分类/模块/处理人/SLA/时间轴，无需为子任务再造一套字段与权限 | **缺"合成一条对客答复"这一环**：Parent 翻转 `split` 后不再挂 hub，`cached_reply_content` 无来源（ADR-0016 的真实缺口） |
| 各 Child 独立毕业 → `hub_dedup` 只见原子单，ADR-0016 的核心目标原样达成 | 工单列表出现 Parent + N 个 Child，列表口径需处理（现有「仅未分配」等筛选未考虑 Parent） |
| 对外契约由 CHECK 约束**硬保证**，不靠调用方自觉 | 回滚已上线的 Hub 子任务 UI（详情页子任务区、行内编辑、确认分流） |
| 符合你的设计意图（agent 在 ticket 上拆） | Child 的 `revert-split` 守卫要求所有 child 仍是 `received`，有进展则不可回滚 |

### 方案 B · hub 层子任务（2026-09-08 现状）

```
Raw ticket ──triage(is_mixed)──> 毕业主 hub + 灌 N 个 draft 子 hub
   └─ ticket (持 source_code) → 主 hub
        ├─ 子 hub#1 (status=draft) → 确认分流 → Bug_fix 推研发
        └─ 子 hub#2 (status=draft) → 确认分流 → Operation AI 答复
```

| 优点 | 缺点 |
|---|---|
| **已建成答复合成 + 出站闸门**：`POST /api/tickets/{id}/reply` 按已答复子任务自动拼接，且「至少一个子任务已答复」才放行出站 | 子任务是 hub 却塞进 ticket 的语义位置，`hub.status` 被迫接纳 `draft`/`answered`/`processing` 三个越界值 |
| 工单列表仍是一行一单，无 Parent/Child 展开问题 | 子任务复用 `hub_issues` 表但走另一套生命周期，与主 hub 的状态机语义分叉 |
| 混合单不再停摆 | `hub_dedup` 见到的是主 hub（仍含多问题），**ADR-0016 的核心目标丢失** |

### 方案 C · 双轨并存（**当前实际状态，最高危**）

自动路径走 B，主管手动 4 端点仍走 A。同一混合单两种产物、两套后续流程。**必须消除，不作为候选**。

---

## 3. 提案：采用 A，并移植 B 已建成的答复合成

**核心判断**：A 与 B 的差异不在"谁更对"，而在 A 缺一环、B 缺一环，且 **A 缺的那环 B 已经写好了**。

- A 缺：Parent 的对客答复合成 → **移植 B 的 `reply` 拼接逻辑**，数据源从「hub 子任务」换成「Parent 的 children 各自的 hub」，闸门条件从「至少一个子任务 answered」换成「至少一个 child 的 hub 已答复」。结构同构，改动集中在一个函数。
- B 缺：`hub_dedup` 只见原子单 → A 天然满足。

**同时必须解决触发混合单积压的原始问题**，否则退回 A 会立刻重现停摆：

1. `split_auto_enabled` 默认改 `True`（agent 自动拆，符合你的设计意图）
2. 保留 `split_auto_confidence` 门槛（0.85）；低于门槛才落主管队列
3. 主管队列必须在工作台可见可操作（拆单提案卡片已存在，确认其未被子任务改造挤掉）

### 3.1 追认保留的转向（不退回）

| 转向 | 处置 | 理由 |
|---|---|---|
| 三道人工闸门 | **追认保留** | AI 准确率不足以无人值守对客发答复；且是可关开关 |
| 转研发默认走飞书 webhook | **追认保留** | 对方系统要求 |
| 入库阶段派单引擎取代 Router | **追认保留**，但需记录副作用：派单规则的产品线/模块两维度失效（派单早于模块判定） |
| ADR-0017 stage / 单一责任人 / 产品内提单 | 保留 | 已独立成 ADR |

---

## 4. 分期实施

| 阶段 | 内容 | 工作量 | 风险 |
|---|---|---|---|
| **P0** | 本 ADR 采纳；`state-machines.md` 标注为现状基线 | 已完成 | — |
| **P1** | 移植答复合成到 Parent：`ticket reply` 数据源改 children-hub，闸门条件同步改；补单测 | 1 天 | 低（同构改造，闸门逻辑不变） |
| **P2** | 恢复主链 agent 自动拆：`run_post_ingest_agents` 重新调用 `execute_split_for_ticket`；`split_auto_enabled` 默认 True；删 `_populate_subtasks_from_triage` | 1 天 | **中** — 需确认拆分后各 Child 的 stage 派生正确（ADR-0017 监听器已覆盖 `split`） |
| **P3** | 拆除 Hub 子任务：删子任务 CRUD 端点与前端子任务区；`hub.status` 去掉 `draft`/`answered`/`processing` 三个越界值；`stage.py` 的 `_HUB_STATUS_DIRECT` 补丁可同步删除 | 1–2 天 | **中高** — 前端详情页改动面大 |
| **P4** | 存量数据迁移：已产生的 hub 子任务转成 Child 工单，或标记为历史只读 | 1 天 | **高** — 取决于生产存量，需先统计 |
| **P5** | 其余 B 类债：`ticket.status` 三死值清理、27 处裸写收口（ADR-0017 §14 路线图） | 2–3 天 | 中 |

**每阶段之间跑 `backend/scripts/reconcile_stage.py` 确认 0 drift。**

### 4.1 动手前必须先查的两个数

P4 的风险完全取决于生产存量，**建议先只读统计再决定**：

```sql
-- 1. 已产生多少 hub 子任务（ticket_id 非空且不是主 hub）
SELECT count(*) FROM hub_issues h
  JOIN tickets t ON t.id = h.ticket_id
 WHERE h.deleted_at IS NULL AND (t.hub_issue_id IS NULL OR t.hub_issue_id <> h.id);

-- 2. 历史 Child 工单还有多少、最近一条什么时候产生
SELECT count(*), max(created_at) FROM tickets WHERE type = 'Child' AND deleted_at IS NULL;
```

若第 1 个数很小（个位数），P4 可直接人工处理；若上百，需要写迁移脚本。

---

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| P2 打开自动拆分后混合单被过度拆分 | `split_auto_confidence` 0.85 门槛保留；先 observe 一周看拆分量与主管 revert 率 |
| P3 前端改动引入回归 | 子任务区与 Child 展开区共存一个发布周期，用开关切换，稳定后再删 |
| 退回 A 后再次出现队列积压 | P2 的前置条件就是「工作台拆单队列可见可操作」，不满足不做 P2 |
| 存量 hub 子任务丢失 | P4 前先跑 4.1 的统计；迁移脚本先 `--dry-run` |

**回滚点**：P1 独立可用（不依赖 P2）；P2 可通过 `split_auto_enabled=False` 秒回退到主管手动；P3 之前 B 方案代码都还在。

---

## 6. 待你拍板

1. **主线**：是否采用方案 A（ticket 层 Child + agent 自动拆）？
2. **P3 力度**：Hub 子任务是「全部拆除」还是「保留为 Operation 类内部拆解、Child 只用于跨类型混合」？（后者会重新引入双轨，不推荐，但如果子任务 UI 已被运营依赖则需权衡）
3. **P4 时机**：先跑 4.1 的统计再定，还是直接按"存量标记只读、新单走 Child"处理？
