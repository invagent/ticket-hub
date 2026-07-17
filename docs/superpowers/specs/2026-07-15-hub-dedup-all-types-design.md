# hub_dedup 全类型覆盖 · 设计文档

- 日期：2026-07-15
- 状态：设计已批准，待写实施计划
- 范围：让所有类型（Operation/Bug_fix/Demand/Internal_task）毕业 hub_issue 时都做语义查重（当前只 Bug_fix/Demand 推 Linear 时才查）。落地 ADR-0016 §2.1「hub_dedup 是唯一主查重，所有类型毕业时查重」。
- 相关：`docs/adr/0016-agent-pipeline-restructure.md` §2.1、`services/hub_issues/hub_dedup.py`、`services/hub_issues/creator.py`、`services/hub_issues/linear_push.py`

## 1. 背景与问题

ADR-0016 §2.1 定：hub_dedup 是唯一主查重，所有类型毕业时对既有 hub_issue 查重，命中则挂靠复用。实际代码只做了一半：

1. `find_duplicate_hub`（`hub_dedup.py:67-72`）候选过滤硬编码 `HubIssue.linear_uuid IS NOT NULL`——只跟已推 Linear 的 hub 合并。Operation/Internal_task 永不推 Linear（linear_uuid 恒 NULL），候选池里没有它们 → 永远查不到重复。
2. `creator.ensure_hub_issue_for_ticket` 毕业路径**完全不调 dedup**。只有 `linear_push._maybe_supersede_duplicate` 在 Bug/Demand 推 Linear 时查。

结果：Operation/Internal_task 重复问题反复毕业成多个 hub_issue，`occurrence_count` 永远 1，去重形同虚设。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| 挂靠语义 | 方案 2：建后 supersede——毕业建 hub 后查重，命中则当前 hub 标 `superseded_by_hub_issue_id`（指向原 hub）+ 原 hub `occurrence_count+1`，ticket 改挂**原 hub** |
| 候选过滤 | 按类型分流：Bug/Demand 保持「跟已推 Linear 的同类合并」；Operation/Internal_task「跟同类型已毕业的合并」（不要求 linear_uuid）。**都加 `type == hub.type` 约束**（不跨类型合并） |
| 共享函数 | `_maybe_supersede_duplicate` 从 linear_push 抽到 hub_dedup 成公共 `maybe_supersede_duplicate`，creator + linear_push 共用 |
| Bug/Demand 查两次 | creator 毕业时查 + linear_push 推前查——linear_push 加「已 superseded 就跳过」防重复查/重复推（保留作兜底） |
| 灰度 | 复用 `hub_dedup_enabled`（默认 true），不新增开关 |
| 降级 | embedding/LLM 失败 → find_duplicate_hub 返回 None → 正常建新 hub，不阻塞毕业（保持现有语义） |

## 3. 改动

### 改动 1：`find_duplicate_hub` 候选过滤按类型（hub_dedup.py）

```python
q = select(HubIssue).where(
    HubIssue.id != hub.id,
    HubIssue.deleted_at.is_(None),
    HubIssue.product_line_code == hub.product_line_code,
    HubIssue.type == hub.type,          # 新增：只跟同类型合并
    HubIssue.embedding.isnot(None),
)
if hub.type in ("Bug_fix", "Demand"):
    q = q.where(HubIssue.linear_uuid.isnot(None))  # 研发类：跟已推 Linear 的合并
# Operation/Internal_task：不加 linear_uuid 约束
rows = db.execute(q).scalars().all()
```

### 改动 2：抽 `maybe_supersede_duplicate` 到 hub_dedup（公共）

把 `linear_push._maybe_supersede_duplicate` 逻辑移到 `hub_dedup.py`：
```python
def maybe_supersede_duplicate(db: Session, hub: HubIssue) -> int | None:
    """查重命中则当前 hub supersede 到原 hub + 原 hub occurrence_count+1。
    返回原 hub_id，否则 None（含降级）。也顺带存好 hub.embedding。"""
    dup_id = find_duplicate_hub(db, hub)
    if dup_id is None:
        return None
    dup = db.get(HubIssue, dup_id)
    if dup is None or dup.deleted_at is not None:
        return None
    now = datetime.now(UTC)
    hub.superseded_by_hub_issue_id = dup_id
    hub.supersede_reason = f"hub-dedup: 与 {dup.short_code} 同一问题，合并复用"
    dup.occurrence_count += 1
    dup.last_seen_at = now
    StatusHistoryRepository(db).record(
        entity_type="hub_issue", entity_id=hub.id,
        from_status=hub.status, to_status=hub.status,
        changed_by="agent:hub_dedup", reason=hub.supersede_reason,
    )
    db.commit()
    return dup_id
```
linear_push 改为 `from ...hub_dedup import maybe_supersede_duplicate`，删本地私有版。

### 改动 3：creator 毕业后调 dedup（所有类型）

`ensure_hub_issue_for_ticket` 在建 hub + flush 后、最终 commit 前：
```python
db.flush()  # hub 已建，有 id
# 全类型查重（受 hub_dedup_enabled 门控）
settings = get_settings()
if settings.hub_dedup_enabled:
    dup_id = maybe_supersede_duplicate(db, hub)
    if dup_id is not None:
        ticket.hub_issue_id = dup_id  # ticket 挂原 hub，而非 superseded 的新 hub
        db.add(TicketHubIssueHistory(
            ticket_id=ticket.id, hub_issue_id=dup_id,
            change_reason=f"hub-dedup 合并到 {dup_id}（{created_by}）",
            human_confirmed=created_by.startswith("user:"),
        ))
        db.commit()
        return HubIssueResult(hub_issue_id=dup_id, ..., created=False)
# 未命中：正常挂新 hub（现有逻辑）
```

### 改动 4：linear_push 防重复（已 superseded 跳过）

`push_hub_issue_to_linear` 调 `maybe_supersede_duplicate` 前，先判 hub 是否已 superseded（creator 已合并过）：
```python
if hub.superseded_by_hub_issue_id is not None:
    return  # creator 毕业时已 hub-dedup 合并，不重复推
```

## 4. 关键设计点

- **ticket 挂原 hub**：命中时 ticket.hub_issue_id 指向 dup_id（有效 hub），当前 superseded hub 不挂 ticket
- **embedding 时机**：`find_duplicate_hub` 内部 `ensure_hub_embedding` 给当前 hub 算并存向量——建 hub 后才有 hub.id，符合方案 2（建后查）
- **不跨类型**：加 `type == hub.type`，Operation 不会误合并到 Bug（现有代码无此约束，理论上同产品线语义相近可能跨类型，一并修）
- **superseded hub 非垃圾**：是审计痕迹（supersede 链 + status_history），可追溯

## 5. 灰度与安全

- `hub_dedup_enabled`（默认 true）门控全部查重
- 降级：embedding/LLM 失败 → None → 建新 hub 不阻塞
- creator 里查重失败不影响毕业（maybe_supersede_duplicate 内部吞降级）

## 6. 测试

- Operation hub 命中同类型 Operation → superseded + occurrence_count+1 + ticket 挂原 hub + created=False
- Operation 不合并到 Bug（type 约束）
- Bug/Demand 仍只跟已推 Linear（linear_uuid）的同类合并
- Internal_task 同类型查重
- embedding 失败 → 不去重正常建 hub
- linear_push 对已 superseded 的 hub 跳过（不重复查/推）
- hub_dedup_enabled=false → creator 不查重
- 未命中 → 正常建新 hub created=True

## 7. 非目标

- 建前查（方案 1）——用方案 2 建后 supersede
- 主管手动 revert supersede（后续）
- 命中后的答复级联（Operation 命中复用答复是另一分支，本次只做「合并挂靠」，不自动复用答复内容）

## 8. 影响面

- 改动：`hub_dedup.py`（候选过滤 + 抽公共函数）、`creator.py`（毕业后查重）、`linear_push.py`（改调公共函数 + 已 superseded 跳过）
- 无迁移（superseded_by_hub_issue_id / occurrence_count 字段已存在）
- 无新配置（复用 hub_dedup_enabled）
- 风险：改核心毕业路径——充分单测 + 灰度默认 true 但降级安全
