# KSM 补料回流处理 · 设计文档

- 日期：2026-07-21
- 范围：**仅 KSM**（智齿同构缺口留待验证后拓展）
- 关联：[[operation-auto-reply-async]]（复用异步 drain）、`request_supply` / `ksm/writeback.py`

## 一、问题

客户在 KSM 侧「补料」是**同一张单（同 `billId`）上的节点流转**，不是新开单。客户补完资料后 KSM 重新推送 webhook，携带的还是同一个 `billId`。

当前 `ksm_ingester.py:73` 的幂等逻辑纯按 `billId` 存在性判断，命中即返回 `deduped=True`，**是一个纯 no-op**：

- 不更新 `source_payload`（客户新补的内容、附件、新节点 id 全部丢弃）
- 不改 status、不写 status_history
- 不重跑任何链路，`webhooks.py:243` 因 `deduped=True` 置 `ingested_ticket_id=None`，`run_post_ingest_agents` 不触发

**后果**：补料的全部内容被静默丢弃。补料回推除了刷新 Redis notice 键，在系统里几乎完全隐形。既不会重复建 hub（billId 幂等挡住了），也不会被识别为补料、更不会重新处理。

## 二、目标

客户补料后 KSM 重推同 billId → 系统识别为**补料回填** → 存下新内容 → **复用异步 drain 自动重答**（replay + answer-router），能答就答、仍不足再补料、答不了转主管。

## 三、核心决策（brainstorm 定案）

| 维度 | 决策 |
|------|------|
| 核心行为 | 更新内容 + 自动重答（复用 Operation 自动答复异步链） |
| 补料识别 | 靠**本地补料状态门控**——只有处于 `awaiting_supply` 的 ticket，回推才算补料回填 |
| 状态层级 | **ticket 层**（补料发出时给每个有源 ticket 置标记；回推命中 ticket 直接看它自己状态） |
| 重答机制 | **入站标记 + 复用异步 drain**——入站只做轻量内容更新 + 状态复位 + 清审计；重答由既有 `drain_operation_auto_reply`（每 2min）自然接管。零新定时任务。 |
| 范围 | **先只做 KSM**，公共逻辑抽干净但只接 KSM |

### `awaiting_supply` 是「已送达」标记，不是「意图」标记

补料请求发出分两步，中间隔异步：

1. `request_supply` → 往 `sync_outbox` 插 `kind='supply'` 行（**只入队，未真发**）
2. KSM writeback drain → `lock → refresh → supplyKsmOrder`（**客户此刻才真收到**）

第 2 步可能不发（`ksm_writeback_dry_run` 默认开，只组装标 skipped）或失败（重试超限标 failed）。因此 `awaiting_supply` **必须在第 2 步真发成功后才置**，含义是「我们确实向客户发出了补料请求，正在等他回」。若在入队时就置，dry_run 未真发时同 billId 的任何回推都会被误判成补料回填、触发不该发生的重答。

## 四、状态机

```
发补料请求：
  request_supply 入 supply outbox（状态不变，维持现状）
      ↓  ⋯ Celery drain 每 2min ⋯
  KSM writeback drain 真发 supplyKsmOrder 成功
      ↓ 【新增落点 writeback.py:240 附近】
  ticket.status: * → awaiting_supply（补料请求已送达客户）+ status_history

补料回推：
  KSM 重推同 billId → _ksm_async_fetch_and_ingest → ingest()
      ↓
  find_by_source 命中 existing ticket【ksm_ingester.py:73】
      ↓ 【新增分支】existing.status == "awaiting_supply"？
         ├─ 是 → apply_supply_refill（回填内容+复位+清审计）→ deduped=True
         └─ 否 → 原 no-op（重复心跳 webhook）→ deduped=True
      ↓
  下一轮 drain_operation_auto_reply（每 2min）扫到该 hub
  （Operation + reply_v=0 + 无 auto_reply 审计）
      ↓
  自动重答（replay + answer-router）：D 答复 / C 再补料 / transfer 留主管
```

## 五、代码改动分解（5 处，无迁移）

### ① 补料回填公共服务（新文件 `app/services/ingest/supply_refill.py`）

跨源共享单元（智齿后续复用），纯机械物化，无 LLM：

```python
def apply_supply_refill(db, existing_ticket, new_payload) -> bool:
    """前置：existing.status == "awaiting_supply"（调用方已判）。
    返回 True=已回填。
    1. existing.source_payload = new_payload（含新补料内容/附件/节点）
    2. body 追加：body += "\n\n[补料回填 {北京时间}]\n{新 content}"
    3. status 复位 awaiting_supply → received，写 status_history
       （changed_by="system:supply_refill"）
    4. 查 existing.hub_issue：
       - hub 不存在/已删 → 只更新内容，不清审计（无 hub 可答），返回 True
       - hub.reply_content_version >= 1 → 已答复过，矛盾状态：只更新内容 +
         记 status_history 留主管，不清审计（不覆盖已发答复），返回 True
       - 否则 → 清该 hub 的 auto_reply AgentDecision（delete，
         subject_type='hub_issue' & subject_id=hub.id & decision_type='auto_reply'），
         返回 True
    """
```

`db.commit()` 由调用方（ingester）负责，与现有 ingest 事务边界一致。

### ② KSM ingester 加补料分支（`ksm_ingester.py:73` 那段）

```python
existing = self._tickets.find_by_source("ksm", bill_id)
if existing is not None:
    if existing.status == "awaiting_supply":
        apply_supply_refill(self._db, existing, payload)
        logger.info("ksm_ingest_supply_refill", bill_id=bill_id, ticket_id=existing.id)
        # deduped=True：不走 post-ingest（避免重跑 triage/分类），
        # 重答完全靠 drain（清审计已在 apply_supply_refill 内完成）
        return _dedup_result(existing, deduped=True)
    # 其他状态 → 原 no-op（重复心跳 webhook）
    logger.info("ksm_ingest_dedup", bill_id=bill_id, existing_ticket_id=existing.id)
    return _dedup_result(existing, deduped=True)
```

两条分支的 `IngestResult` 字段（short_code / customer_id / customer_identity_id /
assigned_user_ids）都从 `existing` 取，与现有 dedup 分支（`ksm_ingester.py:80-90`）
完全一致——抽个 `_dedup_result(existing, *, deduped)` 小助手复用，避免重复。补料
分支与重复心跳分支唯一区别是前者先调了 `apply_supply_refill`。

**关键**：补料回填也返回 `deduped=True`。重答不靠 `ingested_ticket_id`/post-ingest，而是靠 `apply_supply_refill` 清掉 auto_reply 审计后，drain 的既有扫描口径（reply_v=0 且无 auto_reply 审计）自然重新入选该 hub。**完全不碰 triage/分类链，零重分类风险。**

### ③ writeback sender 真发成功后置 awaiting_supply（`writeback.py:240` 附近）

```python
row.status = "sent"; row.sent_at = ...; row.attempts += 1
if action in _CLOSING_ACTIONS:
    self._close_local(row, ticket)
elif action == "supply":
    self._mark_awaiting_supply(row, ticket)   # 新增
self._db.commit()
```

`_mark_awaiting_supply`：`ticket.status` 非终态则置 `awaiting_supply` + 记 status_history（`changed_by="system:ksm_writeback"`，reason 带 outbox id）。终态不动（幂等保护）。不 commit（随外层）。

### ④ 迁移：无需

Ticket.status 是自由字符串（无 CHECK 约束）。`awaiting_supply` 非终态，天然不在各处 `_TICKET_TERMINAL_STATUSES`（`{done,closed,rejected,superseded}`）内，不会被误判为终态。**结论：无需迁移、无需改约束。**

### ⑤ 测试

- `test_supply_refill.py`（新）：
  - awaiting_supply → 回填：source_payload 更新 / body 追加 / 状态复位 received / auto_reply 审计被清
  - hub reply_v≥1（已答复）→ 只更新内容 + 记审计，不清审计（保护已发答复）
  - hub 不存在/已删 → 只更新内容不清审计
- `test_ksm_ingester.py`（改/加）：
  - existing.status=awaiting_supply → 调 apply_supply_refill，deduped=True
  - existing.status=received（重复心跳）→ 原 no-op，不调 refill
- `test_ksm_writeback.py`（加）：
  - supply 真发成功 → ticket 置 awaiting_supply + status_history
  - dry_run（skipped）→ **不**置 awaiting_supply
  - supply 发送失败 → **不**置 awaiting_supply

## 六、边界情况

1. **补料回来时 hub 已答复（reply_v≥1）**：矛盾状态（已答成还发补料请求）。`apply_supply_refill` 只更新内容 + 记审计留主管，**不清审计、不自动重答**，避免覆盖已发答复。
2. **ai_cs 来源**：drain 本就排除 ai_cs 来源（走 reflect），补料回填后也天然不重答。
3. **awaiting_supply 的单被主管手动改状态**：状态已非 awaiting_supply → 回推走原 no-op，不误触发。
4. **hub 已 supersede/删除**：`apply_supply_refill` 查不到 hub → 只更新 ticket 内容，不清审计。

## 七、一致性收益

补料回填让「首次毕业后自动答复」「replay 失败补偿重试」「补料回填后重答」走**完全同一条 drain 路径**（`drain_operation_auto_reply`），无新定时任务、无新触发入口，一致性与可维护性最好。

## 八、非目标（YAGNI）

- 智齿补料回流（同构缺口，本期不做；公共服务 `supply_refill.py` 已为其预留复用）
- 解析 KSM 节点/状态字段做补料识别（改用本地状态门控，跨源通用、不依赖源系统字段语义）
- 「等待补料」的前端展示/主管队列（本期后端闭环优先；有需要再加 hub 层展示标记）
