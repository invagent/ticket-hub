# Operation 自动答复准确率闸门 + 主管审核

**日期**：2026-08-05
**范围**：Operation 自动答复链（`operation_answer.py`）+ op_status 状态机 + 主管工作台
**类型**：新增 AI 答复质量闸门 + 人工审核环节

## 背景

当前 Operation 自动答复链（`operation_answer.py`）流程：

```
replay 生成答复 → answer_router 判 D(可发)/C(需补料)/transfer(留主管)
  → D: 确定性 floor(空/过短/转人工词) → author_reply 发客户 + op_status=answered
  → C: 转兜底主管线下收集补料 + op_status=supplementing
  → transfer: 留主管 + op_status=processing
```

判 D 的答复目前**直接发客户**，只有一层确定性 floor（长度/关键词）兜底，没有对"答复内容准不准"做评估。业务希望：agent 答复后先评估准确率，**高置信直发、低置信转主管人工确认**，主管确认时可修改优化答复再发。

姊妹项目 `01_ticket/ticket-hub` 无同类实现可复用（它只有 FAQ 相似度阈值和 answer-router 语义分类，无答复准确率打分）。faq-review 的"事实性四维审核"思路可借鉴打分 prompt 的维度设计。

## 目标

1. 在 answer_router 判 D 之后新增一个**准确率打分器**（独立 LLM 调用），基于知识库 + FAQ 综合判断答复准不准，输出 0-100 的准确率分。
2. 加**可配置阈值**（默认 90）：≥ 阈值 → 沿用现有逻辑直发客户；< 阈值 → 转主管审核。
3. 新增 op_status `reviewing`（待主管审核答复）：低置信答复存为**草稿**（不发客户），进主管审核队列。
4. 主管在审核队列可**编辑**答复草稿后发送 → 走现有 `/reply` → `answered`（发客户）。

## 非目标

- 不改 C / transfer 分支逻辑。
- 不改研发类（Bug_fix/Demand）路径。
- reviewing 态**只有"编辑后发送"一条出路**（不提供"打回处理/丢弃草稿转 processing"出口——用户明确本期不做）。
- 不改姊妹项目。

## 最终状态机（6 态）

在现有 5 态（`processing / supplementing / answered / closed / exception`）基础上**加回 `reviewing`**。

> 说明：这与上一轮删除的 `resupplied` 性质不同。`resupplied` 是"等客户重推"的被动中间态（仅为触发重扫而存在，已删）；`reviewing` 是"有明确 agent 草稿等主管决策"的主动待办态，对应真实人工队列与 UI，语义独立成立。

```
   [毕业] processing ── agent 答复链 ──┬─ D & 确定性floor过 & accuracy≥阈值 ─→ answered（发客户，现有逻辑不变）
                                       │
                                       ├─ D & 确定性floor过 & accuracy<阈值 ─→ reviewing（存草稿不发，转兜底主管）
                                       │        │
                                       │        └─ 主管编辑后发送 POST /reply ─→ answered（发客户）
                                       │
                                       ├─ C ─→ supplementing（转主管线下收集补料）
                                       └─ transfer / floor不过 / replay失败 ─→ processing / exception
```

## 详细设计

### 1. 准确率打分器（新增）

**新文件 `app/services/agents/answer_accuracy.py`**（照抄 `_route_answer` 骨架）：
- 函数 `score_answer_accuracy(question: str, answer: str, cited_knowledge: list[dict], *, router: LLMRouter | None = None) -> AccuracyScore`
- `AccuracyScore` dataclass（frozen）：`accuracy: int`（0-100）、`reason: str`
- 输入组装：客户问题 + agent 答复 + `cited_knowledge`（引用的 FAQ/知识条目原文）。
- LLM `temperature=0.0`，`response_format=json_object`，输出 `{accuracy: 0-100, reason: "..."}`。
- **异常/非法一律兜底 `accuracy=0`**（打分失败视作低置信 → 转主管，安全侧）。仿 `_route_answer` 的 transfer 兜底。

**新 prompt `prompts/answer_accuracy.md`**：
- 让 LLM 依据**知识库 + FAQ 综合判断**答复准确率（不只看 cited_knowledge，也用其对该领域的理解），借鉴 faq-review 的事实性维度：答复是否有知识依据、是否与引用知识一致、是否答非所问、是否含臆造信息。
- 输出严格 JSON：`{"accuracy": <0-100 整数>, "reason": "<简短中文理由>"}`。

**打分数据源说明**：`ReplayResult`（`adapters/ai_cs/types.py`）已返回 `cited_knowledge: list[dict]` 和 `skills_used`，但**无现成置信度分**。打分器消费 `cited_knowledge` 作为依据，`replay` 调用处需把它透传给打分器（当前 `_replay_with_retry` 只返回 `answer` 字符串，需改为返回完整 `ReplayResult` 或额外带出 `cited_knowledge`）。

### 2. 配置项（三态模式）

**`app/config.py`** 新增：
- `operation_answer_accuracy_mode: str = "off"`（三态模式，默认 off）
- `operation_answer_accuracy_threshold: int = 90`（阈值，0-100）

`operation_answer_accuracy_mode` 三个取值（用 CheckConstraint 或代码校验限定）：

| 模式 | 打分 | <阈值行为 | 用途 |
|---|---|---|---|
| `off`（默认） | 不打分 | 直发（完全同现状） | 未启用，灰度可回退 |
| `observe`（观察期） | **打分** | **仍直发客户**，只记审计/日志 | 上线前采集真实分数分布，校准阈值，不影响客户体验 |
| `enforce`（启闸） | 打分 | 存草稿 + reviewing + 转主管 | 正式启用闸门 |

设计意图：`observe` 让我们先"只打分记日志、不启闸"跑一段真实流量，看 accuracy 分布落在哪，再决定 90 这个阈值合不合适、要不要调，然后才切 `enforce`。observe 期对客户零影响（照常直发），纯采集。

阈值判定（仅 enforce 生效）：`accuracy >= operation_answer_accuracy_threshold` → 直发；否则 → reviewing。

### 3. D 分支改造（`operation_answer.py:236-249`）

现状 D 分支：floor 过 → `author_reply` 发 → `op_status=answered`。

改为：
```
if route.branch == "D":
    if not _is_answer_sendable(answer, settings):
        return _transfer("...确定性 floor...")

    mode = settings.operation_answer_accuracy_mode  # off | observe | enforce
    # off 以外都打分
    if mode in ("observe", "enforce"):
        score = score_answer_accuracy(question, answer, cited_knowledge)
        # enforce 且低置信 → 存草稿转审核（唯一不直发的分支）
        if mode == "enforce" and score.accuracy < settings.operation_answer_accuracy_threshold:
            _save_draft_reply(db, hub, content=answer)
            apply_op_status(db, hub, to_status=OP_REVIEWING,
                            handler=resolve_supervisor_name(db, settings),
                            reason=f"准确率 {score.accuracy}% < {阈值}%，待主管审核")
            _record_decision(db, hub.id, branch="D_review", question=question,
                             answer=answer, supply_note="",
                             extra={"accuracy": score.accuracy, "reason": score.reason})
            logger.info("operation_answer_low_accuracy_review", hub_issue_id=hub.id, accuracy=score.accuracy)
            return True
        # observe（任何分数）或 enforce 且 ≥阈值：直发，但把分数记进审计/日志
        logger.info("operation_answer_accuracy_scored", hub_issue_id=hub.id,
                    mode=mode, accuracy=score.accuracy)
        _accuracy_extra = {"accuracy": score.accuracy, "reason": score.reason, "mode": mode}
    else:
        _accuracy_extra = None

    # off / observe / enforce-达标：现有逻辑直发
    author_reply(db, hub.id, content=answer, authored_by="agent:ai_cs")
    apply_op_status(db, hub, to_status=OP_ANSWERED, handler="agent", reason="agent 答复成功")
    _record_decision(db, hub.id, branch="D", question=question, answer=answer,
                     supply_note="", extra=_accuracy_extra)
    return True
```

关键点：`observe` 模式下打分照跑、分数进审计（`branch="D"` 的 decision 带 accuracy），但**答复照常直发客户**——纯采集，零客户影响。只有 `enforce` + 低置信才走存草稿/reviewing 分支。

`_record_decision` 需扩一个可选 `extra: dict | None = None` 参数，把 accuracy/reason 存进 `proposal`（审计）。

### 4. 草稿存储与"未发"标记（关键技术细节）

草稿存 `hub.reply_content`，但**必须区分"草稿未发" vs "已发答复"**，否则有误发风险。

**风险点**（已勘察）：`reply_content` 有两类读取者——
- outbox drain 发客户（`ksm/writeback.py` `_reply_text`、`zhichi/writeback.py`）：读的是 **outbox row 的 payload**，草稿只要不入 outbox 就不会被发。✅
- **`_released_text`（`ksm/writeback.py:429-433`）**：released 关单时直接读 `hub.reply_content` 当关单话术。若草稿态 hub 走 status→released 级联，**未审核草稿会被当关单话术发客户**。⚠️ 必须防。

**方案**：新增字段 `hub_issues.reply_is_draft: bool`（默认 False）。
- `_save_draft_reply`：写 `hub.reply_content = answer` + `reply_is_draft = True`，**不级联、不入 outbox**（不调 `author_reply`，只写草稿字段 + reply_authored_by='agent:ai_cs:draft'）。
- `_released_text`（writeback.py:431）：改为 `if hub.reply_content and not hub.reply_is_draft:` 才用，草稿态回落 `_DEFAULT_RELEASED_NOTE`。
- `author_reply`（主管发送时）：写 reply_content 后**清 `reply_is_draft = False`**（转正式已发）。
- 迁移新增 `reply_is_draft` 列（默认 False，存量全 False，语义正确）。

### 5. 主管审核 API + 队列

**审核队列**（复用 `pending-hub-issues` 模式）：
- 新增 `GET /api/supervisor/reviewing-answers`（require_supervisor）：列 `type=Operation & op_status=reviewing & 未删除`，带 hub short_code、agent 草稿（reply_content）、最近一条 `D_review` 决策的 accuracy/reason。

**发送**：复用现有 `POST /api/hub-issues/{id}/reply`（Task 4 已实现，主管编辑后提交）：
- 主管把（可能编辑过的）内容 POST 上去 → `author_reply` 级联 + 入 outbox 发客户 + 清 `reply_is_draft` → `apply_op_status(→answered)`。
- **前置约束调整**：现有 `/reply` 端点对 `op_status=closed` 拒绝（Task 4）。reviewing 态允许 reply（正是它的出路），无需额外改动——reviewing 不在拒绝名单。
- reviewing 态经 `/reply` 后 op_status: reviewing → answered（`apply_op_status` 幂等入口，正常变更）。

### 6. 前端

- **主管工作台**：新增"待审核答复"卡片/队列（区别于 pending Linear、拆单提案等既有卡片；颜色另取一色，与紫/青/琥珀/黄区分）。展示：short_code、客户问题、agent 草稿、准确率分 + 理由，一个可编辑文本框 + 「发送」按钮（调 `/reply`）。
- **OpStatusBadge**：新增 `reviewing` 徽章（文案如"待审核"）。
- 工单列表 / 详情页 op_status 筛选项加 `reviewing`。

## 影响面清单

| 文件 | 改动 |
|---|---|
| `app/services/agents/answer_accuracy.py` | 新建：打分器 + AccuracyScore |
| `prompts/answer_accuracy.md` | 新建：打分 prompt |
| `app/config.py` | +2 配置（accuracy_mode: off/observe/enforce、accuracy_threshold）|
| `app/services/hub_issues/op_status.py` | +OP_REVIEWING 常量 + _VALID |
| `app/models.py` | op_status CheckConstraint 加 reviewing；+reply_is_draft 列 |
| `migrations/versions/0028_*.py` | 新迁移：约束加 reviewing + reply_is_draft 列（down_revision=0027_feishu_ai_source；注意 0027 是已存在的 feishu_ai 迁移，本迁移接在其后）|
| `app/services/agents/operation_answer.py` | D 分支加闸门；_replay 带出 cited_knowledge；_save_draft_reply；_record_decision +extra |
| `app/services/ksm/writeback.py` | _released_text 尊重 reply_is_draft |
| `app/services/zhichi/writeback.py` | 同上（对称，若有 released 读 reply_content 路径）|
| `app/services/cascade/reply_sync.py` | author_reply 发送时清 reply_is_draft |
| `app/api/supervisor.py` | +GET /reviewing-answers |
| `frontend/src/components/OpStatusBadge.tsx` | +reviewing 徽章 |
| `frontend/src/pages/.../SupervisorPage.tsx` | +待审核答复队列卡片 |

## 测试

- **打分器单测**：mock LLM 返回 → 解析 accuracy/reason；异常/非法 JSON → accuracy=0 兜底。
- **D 分支闸门单测**：
  - mode=off → 行为同现状（直发，不打分）。
  - mode=observe（任意分数）→ 打分 + author_reply 发 + answered + 审计带 accuracy（**低分也直发**，纯采集）。
  - mode=enforce + accuracy≥阈值 → author_reply 发 + answered + 审计带 accuracy。
  - mode=enforce + accuracy<阈值 → 存草稿(reply_content 有值 + reply_is_draft=True) + op_status=reviewing + **无 outbox 行**（不发客户）+ 审计带 accuracy。
- **草稿安全单测**：reviewing 态 hub 若走 released 级联，`_released_text` 回落默认话术（不发草稿）。
- **主管发送单测**：reviewing 态 POST /reply → author_reply 级联 + outbox + reply_is_draft 清零 + op_status=answered。
- **队列单测**：GET /reviewing-answers 返回 reviewing 单 + accuracy/理由。
- **回归**：C / transfer / closed 拒绝 / T+7 自动关 等既有链路不受影响。

## 迁移与上线

1. `git pull` + `alembic upgrade head`（0028：约束加 reviewing + reply_is_draft 列）。
2. 重启后端 + 前端 rebuild。
3. **三阶段灰度**：`off`（同现状）→ `observe`（打分记审计、照常直发，跑真实流量采集 accuracy 分布，校准阈值）→ `enforce`（正式启闸，<阈值转审核）。阈值先 90，据 observe 期实际分布调。
4. 无数据回填（reply_is_draft 存量全 False，op_status 无 reviewing 存量）。

## 待确认的实现细节（非阻塞）

- reviewing 徽章文案最终用词（实现时定）。
- （已纳入正式设计）观察期开关 = `accuracy_mode` 的 `observe` 态。
- `_replay_with_retry` 改为返回 `ReplayResult`（带 cited_knowledge）还是额外参数带出——实现时按最小改动定。
