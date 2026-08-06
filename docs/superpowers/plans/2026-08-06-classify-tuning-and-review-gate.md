# 分类优化 + 研发类推 Linear 前人工确认闸门 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** triage 分类优化（基础平台报错优先 Operation）+ 研发类（Bug_fix/Demand）自动毕业后推 Linear 前加主管确认闸门 + 主管重分类/误报关闭。

**Architecture:** 三块。① triage prompt 改规则4「报错性质三分」+ few-shot（不改代码，draft→回放→promote）。② `require_review_before_linear` 开关（默认开）：自动路径毕业的研发类置 `hub.status='pending_review'` 不推 Linear。③ 主管队列端点 + 三动作（确认→推Linear / 改判→Operation回炉答复链 / 误报关闭）。无 DB 迁移。

**Tech Stack:** FastAPI + SQLAlchemy + pytest（SQLite in-memory）。后端命令在 `backend/`：`.venv/bin/pytest <path> -v`、`make lint`。前端 `frontend/`：`npm run type-check`。

**Spec:** `docs/superpowers/specs/2026-08-06-classify-tuning-and-review-gate-design.md`

## Global Constraints

- 后端命令在 `backend/`；单测 SQLite in-memory（`db_session` fixture），schema 由 `app/models.py` metadata 建。
- **无 DB 迁移**：`hub.status` 无 CheckConstraint（自由字符串），`pending_review` 直接用；`op_status=processing` 是既有合法值。
- op_status 变更经 `apply_op_status`（唯一入口）。
- **`pending_review` 是新状态值**，勿与既有 `status='pending'`（Linear 推送失败待人工队列，`pending-hub-issues` 端点消费）混淆——两者是不同队列、不同状态值。
- 闸门**只加在自动路径** `create_hub_issue_for_ticket_auto`（`creator.py`）；主管手动 `create-hub-issue` 端点（`supervisor.py`，有自己独立的 push 调用）**故意不加闸门、保持直推**，勿误改。
- 改判成 Operation：设 `type=Operation` + `op_status=processing` + `op_handler=agent` → 下轮 `drain_operation_auto_reply` beat 自动扫到跑答复链（复用现有，零新答复代码）。
- prompt 优化走 `skill_prompts` 三槽（draft→`validate_draft` 回放→promote），不改代码不发版；本 plan 的 Task 1 只产出 draft 内容 + 验证，promote 由用户在 UI 确认。

---

### Task 1: triage prompt 优化（draft 内容 + 回放验证）

改 `prompts/triage.md` 规则4 + few-shot，作为 **draft** 提交验证。不动 current（避免未验证就影响线上）。

**Files:**
- Modify（作为 draft 内容起草）: `backend/prompts/triage.md`
- 验证工具: `backend/app/services/skills/draft_validator.py`（`validate_draft(db, name, sample=8)` 已存在，不改）

**Interfaces:**
- Produces: 一份改好的 triage prompt 文本（规则4 三分 + 新 few-shot）。

- [ ] **Step 1: 改规则 4 为「报错性质三分」**

`prompts/triage.md` 第 28-30 行（规则4「报错提示区分性质」）替换为：

```markdown
4. **报错性质三分**：
   (a) 明确业务原因（权限不足、资质限制、「不支持在 X 模式下开具」）→ Operation；
   (b) **基础平台鉴权/连接/部署类报错**——`app_token/token 异常`、`密钥/appid/secret 错误`、
       `鉴权失败`、`初始化失败`、`XX 无法调用 YY`、`连接/对接失败`、`参数配置错误`、
       `证书过期`、`plugin not found`（多为包未部署）——**优先 Operation**（大概率客户侧
       配置/凭证/部署问题，由 AI 客服/主管先核查配置）；
   (c) **仅明确的程序级异常**——空指针（NullPointerException）、数组越界、堆栈报错、500、
       「服务器异常」且无配置嫌疑——才判 Bug_fix。
   **例外升级回 Bug_fix**：客户明确说明「配置已核对无误仍报错」，或伴随 (c) 类程序异常证据。
```

- [ ] **Step 2: 改冲突的旧 few-shot（plugin not found）**

`prompts/triage.md` 第 77-82 行的 few-shot 现在是 `发票云接口报 mservice plugin not found → Bug_fix 0.95`，与新规则 (b) 矛盾。改为 Operation：

```markdown
输入：
title="发票云接口报 mservice plugin not found"
body="调用 /imc/api 时报 mservice not find"
product_line="cloud-fapiao", module="接口集成"

输出：`{"type":"Operation","confidence":0.82,"reason":"plugin not found 多为包未部署，先核查部署","is_mixed":false,"sub_problems":[]}`

（要点：plugin not found / mservice not find 通常是补丁包未部署或配置未生效，属基础部署问题，
先归 Operation 让主管/AI 客服核查部署。只有伴随明确堆栈/空指针等程序异常才判 Bug_fix。）
```

- [ ] **Step 3: 新增 app_token / 初始化 few-shot（5915 类）**

在 few-shot 区追加（保持 `---` 分隔格式）：

```markdown
输入：
title="移动云初始化失败，获取发票云平台app_token异常"
body="移动云无法调用发票云：获取发票云平台app_token异常"
product_line="cloud-fapiao", module="云应用参数配置"

输出：`{"type":"Operation","confidence":0.85,"reason":"app_token/初始化报错，优先排查配置凭证","is_mixed":false,"sub_problems":[]}`

（要点：app_token 异常、初始化失败、XX 无法调用 YY 这类基础平台鉴权/连接报错，绝大多数是
客户侧 appid/secret/参数配置填错——优先 Operation。基础平台若真为 bug 影响面极大早会爆发，
单客户报错先验偏配置问题。除非客户说「配置已核对无误仍报错」或带程序级堆栈才升级 Bug_fix。）
```

- [ ] **Step 4: 起草说明 + 交付**

不 promote、不改代码。把改好的 `prompts/triage.md` 作为**待验证 draft**。在报告里写明：验证方式为管理员在 skill 后台把此内容存为 triage draft → `POST draft/validate`（内部调 `validate_draft`，用真实工单 current vs draft 回放）→ 确认 5915 类翻 Operation、历史 Bug_fix 无大面积回归 → promote。这一步是**人工在 UI 操作**（需真实工单 + LLM key），不在自动化测试内。

- [ ] **Step 5: 提交**

```bash
git add backend/prompts/triage.md
git commit -m "feat(triage): 规则4报错性质三分 — 基础平台报错(token/初始化/plugin)优先Operation"
```

注：此提交改的是 `prompts/triage.md` 文件（文件兜底版本）。线上生效仍需走 skill draft→promote（DB 覆盖优先于文件）。文件与 DB 双写是项目现状（`load_prompt` DB>文件）。

---

### Task 2: `require_review_before_linear` 开关 + 自动路径闸门

**Files:**
- Modify: `backend/app/config.py`（+开关）
- Modify: `backend/app/services/hub_issues/creator.py`（`create_hub_issue_for_ticket_auto` 加闸门 + `_mark_pending_review`）
- Test: `backend/tests/unit/services/test_hub_issue_creator.py`

**Interfaces:**
- Consumes: `push_hub_issue_to_linear`（现有）。
- Produces: `settings.require_review_before_linear: bool = True`；自动路径毕业的 Bug_fix/Demand 在开关开时 `hub.status='pending_review'` 且**不调** `push_hub_issue_to_linear`。

- [ ] **Step 1: 加配置**

`app/config.py`（Linear 段附近，`linear_push_enabled` 旁）：

```python
    # 研发类(Bug_fix/Demand)自动毕业后，推 Linear 前是否需主管确认分类。
    # 默认开：agent 自动毕业的研发类进 pending_review 待确认队列，不自动推 Linear。
    # 主管手动毕业(create-hub-issue)不受此闸门影响，视为已确认直推。
    require_review_before_linear: bool = True
```

- [ ] **Step 2: 写失败测试**

`tests/unit/services/test_hub_issue_creator.py` 加（参考文件现有 fixture/构造工单的写法；`_S`/settings 注入方式以文件实际为准）：

```python
def test_auto_bugfix_gated_to_pending_review(db_session, monkeypatch) -> None:
    """require_review_before_linear=True：自动毕业的 Bug_fix → status=pending_review，不推 Linear。"""
    from app.services.hub_issues import creator as mod

    pushed = []
    monkeypatch.setattr(mod, "push_hub_issue_to_linear", lambda hid: pushed.append(hid))
    # 构造一张 predicted_type=Bug_fix 的 ticket（沿用文件里已有的建 ticket helper）
    ticket = _make_classified_ticket(db_session, predicted_type="Bug_fix", confidence=0.95)
    db_session.commit()
    # 确保开关开（默认 True；若测试用 settings 覆盖则显式设 True）
    result = mod.create_hub_issue_for_ticket_auto(ticket.id)
    assert result is not None and result.type == "Bug_fix"
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    db_session.refresh(hub)
    assert hub.status == "pending_review"
    assert pushed == []  # 未推 Linear


def test_auto_operation_not_gated(db_session, monkeypatch) -> None:
    """Operation 自动毕业不受闸门影响（本就不推 Linear，status=created）。"""
    from app.services.hub_issues import creator as mod
    ticket = _make_classified_ticket(db_session, predicted_type="Operation", confidence=0.9)
    db_session.commit()
    result = mod.create_hub_issue_for_ticket_auto(ticket.id)
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "created"
```

注：`create_hub_issue_for_ticket_auto` 内部 `make_session()` 自建 session，测试里 `monkeypatch` push 函数即可验证「是否推」。若测试需控制开关值，看 `get_settings` 在该函数内的调用方式（可能需 monkeypatch `mod.get_settings` 或用 env）。`_make_classified_ticket` 若文件无现成 helper，按文件里已有的建 ticket 模式内联构造（predicted_type/predicted_confidence/title/status='received'）。

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_hub_issue_creator.py::test_auto_bugfix_gated_to_pending_review -v`
Expected: FAIL（当前自动路径直推 Linear，hub.status=created）

- [ ] **Step 4: 实现闸门**

`creator.py` `create_hub_issue_for_ticket_auto`（现 217-219）：

```python
    if result.created and result.type in ("Bug_fix", "Demand"):
        if get_settings().require_review_before_linear:
            _mark_pending_review(result.hub_issue_id)
        else:
            push_hub_issue_to_linear(result.hub_issue_id)
    return result
```

新增 `_mark_pending_review`（自开 session 或复用；与 auto 函数同风格，写 status + status_history）：

```python
def _mark_pending_review(hub_issue_id: int) -> None:
    """研发类自动毕业 → pending_review 待主管确认（不推 Linear）。"""
    from app.repositories.status_history import StatusHistoryRepository

    db = make_session()
    try:
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None:
            return
        prev = hub.status
        hub.status = "pending_review"
        StatusHistoryRepository(db).record(
            entity_type="hub_issue", entity_id=hub.id,
            from_status=prev, to_status="pending_review",
            changed_by="agent:hub_issue_auto",
            reason="研发类待主管确认分类后推 Linear",
        )
        db.commit()
    finally:
        db.close()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_hub_issue_creator.py -v`
Expected: PASS（新 2 条 + 既有回归）

- [ ] **Step 6: 提交**

```bash
git add app/config.py app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator.py
git commit -m "feat(creator): 研发类自动毕业推Linear前置pending_review闸门(require_review_before_linear)"
```

---

### Task 3: 主管队列端点 `GET /pending-classification`

**Files:**
- Modify: `backend/app/api/supervisor.py`
- Test: `backend/tests/unit/test_supervisor_pending_classification.py`（新建）

**Interfaces:**
- Produces: `GET /api/supervisor/pending-classification`（require_supervisor）→ `{items:[{hub_issue_id, short_code, type, title, body, predicted_type, confidence, reason}]}`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_pending_classification.py`（`_bearer` + fixture 参考 `test_hub_issue_reply_api.py`）：

```python
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.api.auth import issue_jwt
from app.models import HubIssue, User


def _bearer(uid, *, name="carol", role="supervisor"):
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pc_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(HubIssue(id=60, short_code="HUB-000060", type="Bug_fix",
                            title="app_token异常", canonical_body="初始化失败",
                            status="pending_review", predicted_type="Bug_fix"))
    db_session.add(HubIssue(id=61, short_code="HUB-000061", type="Operation",
                            title="配置咨询", status="created"))  # 不该出现在队列
    db_session.commit()
    return db_session


def test_pending_classification_requires_supervisor(app_client: TestClient, pc_world) -> None:
    r = app_client.get("/api/supervisor/pending-classification",
                       headers=_bearer(1, name="bob", role="member"))
    assert r.status_code == 403


def test_pending_classification_lists_only_pending_review_devtype(app_client: TestClient, pc_world) -> None:
    r = app_client.get("/api/supervisor/pending-classification", headers=_bearer(2))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["short_code"] == "HUB-000060"
    assert items[0]["type"] == "Bug_fix"
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/test_supervisor_pending_classification.py -v`
Expected: FAIL（端点不存在 404）

- [ ] **Step 3: 实现端点**

`supervisor.py`（参考 `list_pending_hub_issues` 783 行范式）：

```python
class PendingClassificationItem(BaseModel):
    hub_issue_id: int
    short_code: str
    type: str
    title: str
    body: str | None
    predicted_type: str | None
    confidence: float | None
    reason: str | None


class PendingClassificationResponse(BaseModel):
    items: list[PendingClassificationItem]


@router.get("/pending-classification", response_model=PendingClassificationResponse)
def list_pending_classification(
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 50,
) -> PendingClassificationResponse:
    """研发类(Bug_fix/Demand) status=pending_review 待主管确认分类队列。"""
    hubs = (
        db.query(HubIssue)
        .filter(HubIssue.deleted_at.is_(None), HubIssue.status == "pending_review",
                HubIssue.type.in_(("Bug_fix", "Demand")))
        .order_by(HubIssue.updated_at.desc())
        .limit(min(limit, 100))
        .all()
    )
    items = []
    for h in hubs:
        # 关联 ticket 的最近 classify_type 决策拿 reason/confidence
        tk = db.query(Ticket).filter(Ticket.hub_issue_id == h.id, Ticket.deleted_at.is_(None)).first()
        reason = None
        conf = None
        if tk is not None:
            dec = (db.query(AgentDecision)
                   .filter(AgentDecision.subject_type == "ticket", AgentDecision.subject_id == tk.id,
                           AgentDecision.decision_type == "classify_type")
                   .order_by(AgentDecision.id.desc()).first())
            if dec and dec.proposal:
                reason = dec.proposal.get("reason")
                conf = dec.proposal.get("confidence")
        items.append(PendingClassificationItem(
            hub_issue_id=h.id, short_code=h.short_code, type=h.type, title=h.title,
            body=h.canonical_body, predicted_type=h.type, confidence=conf, reason=reason,
        ))
    return PendingClassificationResponse(items=items)
```

（确认 `Ticket`/`AgentDecision`/`HubIssue`/`BaseModel`/`require_supervisor` 已 import。）

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/pytest tests/unit/test_supervisor_pending_classification.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/supervisor.py tests/unit/test_supervisor_pending_classification.py
git commit -m "feat(supervisor): 待确认分类队列 GET /pending-classification"
```

---

### Task 4: 三动作端点（confirm / reclassify / dismiss）

**Files:**
- Modify: `backend/app/api/supervisor.py`
- Test: `backend/tests/unit/test_supervisor_classification_actions.py`（新建）

**Interfaces:**
- Consumes: `push_hub_issue_to_linear`、`apply_op_status`（+OP_PROCESSING）。
- Produces: 3 端点（均 require_supervisor）：
  - `POST /confirm-classification` `{hub_issue_id}` → status created + push Linear
  - `POST /reclassify` `{hub_issue_id, new_type, reason}` → 改 type（Operation 回炉答复链）
  - `POST /dismiss-classification` `{hub_issue_id, reason}` → status closed

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_classification_actions.py`：

```python
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import patch
from app.api.auth import issue_jwt
from app.models import HubIssue, User


def _bearer(uid, *, name="carol", role="supervisor"):
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def act_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    for hid, sc in [(70, "HUB-000070"), (71, "HUB-000071"), (72, "HUB-000072")]:
        db_session.add(HubIssue(id=hid, short_code=sc, type="Bug_fix",
                                title="t", canonical_body="b", status="pending_review",
                                predicted_type="Bug_fix"))
    db_session.commit()
    return db_session


def test_confirm_pushes_linear(app_client: TestClient, act_world: Session) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post("/api/supervisor/confirm-classification",
                            json={"hub_issue_id": 70}, headers=_bearer(2))
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 70); act_world.refresh(hub)
    assert hub.status == "created"
    push.assert_called_once_with(70)


def test_reclassify_to_operation_enters_answer_chain(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post("/api/supervisor/reclassify",
                        json={"hub_issue_id": 71, "new_type": "Operation", "reason": "配置问题"},
                        headers=_bearer(2))
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 71); act_world.refresh(hub)
    assert hub.type == "Operation"
    assert hub.status == "created"
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"  # 下轮 drain 会扫到


def test_dismiss_closes(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post("/api/supervisor/dismiss-classification",
                        json={"hub_issue_id": 72, "reason": "误报"}, headers=_bearer(2))
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 72); act_world.refresh(hub)
    assert hub.status == "closed"


def test_actions_require_supervisor(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post("/api/supervisor/confirm-classification",
                        json={"hub_issue_id": 70}, headers=_bearer(1, name="bob", role="member"))
    assert r.status_code == 403
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/test_supervisor_classification_actions.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现三端点**

`supervisor.py`（`apply_op_status`/`OP_PROCESSING` 从 `app.services.hub_issues.op_status` import）：

```python
class ConfirmClassificationBody(BaseModel):
    hub_issue_id: int

class ReclassifyBody(BaseModel):
    hub_issue_id: int
    new_type: str = Field(..., pattern="^(Operation|Bug_fix|Demand|Internal_task|Complaint)$")
    reason: str = Field("", max_length=500)

class DismissClassificationBody(BaseModel):
    hub_issue_id: int
    reason: str = Field("", max_length=500)

class ClassificationActionResponse(BaseModel):
    hub_issue_id: int
    status: str
    type: str


def _get_pending_review_hub(db: Session, hub_issue_id: int) -> HubIssue:
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_issue_id} not found")
    if hub.status != "pending_review":
        raise HTTPException(status_code=409, detail=f"hub {hub.short_code} status={hub.status!r} 非 pending_review")
    return hub


@router.post("/confirm-classification", response_model=ClassificationActionResponse)
def confirm_classification(body: ConfirmClassificationBody, background_tasks: BackgroundTasks,
                           user: AuthedUser = Depends(require_supervisor),
                           db: Session = Depends(get_session)) -> ClassificationActionResponse:
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    prev = hub.status
    hub.status = "created"
    StatusHistoryRepository(db).record(entity_type="hub_issue", entity_id=hub.id,
        from_status=prev, to_status="created", changed_by=f"user:{user.name}", reason="主管确认分类")
    db.commit()
    background_tasks.add_task(push_hub_issue_to_linear, hub.id)
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)


@router.post("/reclassify", response_model=ClassificationActionResponse)
def reclassify(body: ReclassifyBody, user: AuthedUser = Depends(require_supervisor),
               db: Session = Depends(get_session)) -> ClassificationActionResponse:
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    old_type = hub.type
    hub.type = body.new_type
    # 分类修正审计（写在关联 ticket 上，human_confirmed）
    tk = db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).first()
    if tk is not None:
        db.add(AgentDecision(decision_type="classify_type", subject_type="ticket", subject_id=tk.id,
            proposal={"predicted_type": body.new_type, "reason": body.reason or f"主管改判 {old_type}→{body.new_type}",
                      "skill": "manual", "human_confirmed": True, "changed_by": f"user:{user.name}"}))
    if body.new_type == "Operation":
        hub.status = "created"
        apply_op_status(db, hub, to_status=OP_PROCESSING, handler="agent",
                        reason=f"主管改判 {old_type}→Operation，回炉答复链")
    elif body.new_type in ("Bug_fix", "Demand"):
        hub.status = "pending_review"  # 改完仍待确认才推 Linear
    else:  # Internal_task / Complaint：不推 Linear、不走答复
        hub.status = "created"
    StatusHistoryRepository(db).record(entity_type="hub_issue", entity_id=hub.id,
        from_status="pending_review", to_status=hub.status,
        changed_by=f"user:{user.name}", reason=f"改判 {old_type}→{body.new_type}: {body.reason}")
    db.commit()
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)


@router.post("/dismiss-classification", response_model=ClassificationActionResponse)
def dismiss_classification(body: DismissClassificationBody, user: AuthedUser = Depends(require_supervisor),
                           db: Session = Depends(get_session)) -> ClassificationActionResponse:
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    prev = hub.status
    hub.status = "closed"
    StatusHistoryRepository(db).record(entity_type="hub_issue", entity_id=hub.id,
        from_status=prev, to_status="closed", changed_by=f"user:{user.name}",
        reason=f"主管判误报关闭: {body.reason}")
    db.commit()
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)
```

（确认 import：`Field`(pydantic)、`BackgroundTasks`、`StatusHistoryRepository`、`AgentDecision`、`Ticket`、`apply_op_status`、`OP_PROCESSING`。）

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/pytest tests/unit/test_supervisor_classification_actions.py -v`
Expected: PASS（4 条）

- [ ] **Step 5: lint**

Run: `.venv/bin/ruff check app/api/supervisor.py && .venv/bin/mypy app/api/supervisor.py`
Expected: clean

- [ ] **Step 6: 提交**

```bash
git add app/api/supervisor.py tests/unit/test_supervisor_classification_actions.py
git commit -m "feat(supervisor): 分类三动作 confirm/reclassify(Operation回炉)/dismiss"
```

---

### Task 5: 前端待确认分类队列

**Files:**
- Modify: `frontend/src/pages/workbench/WorkbenchPage.tsx`（+队列）
- Modify: `frontend/src/api/types.ts`（gen）
- Modify: hub 状态展示（`pending_review` 徽章，位置以现有 hub status 展示组件为准）

**Interfaces:** 消费 4 个新端点。

- [ ] **Step 1: 生成类型**

Run（repo 根）: `make gen-types`
Expected: types.ts 含 pending-classification / confirm-classification / reclassify / dismiss-classification。

- [ ] **Step 2: 加队列组件**

`WorkbenchPage.tsx`：新增「待确认分类」队列（mirror 现有 reviewing/pending 队列的 useQuery + useMutation + invalidate 写法 —— 先读文件确认现有队列组件模式）。每卡显示 short_code、title、body 摘要、type + confidence + reason，三个操作：
- 「确认推送」→ POST confirm-classification
- 「改判」→ 下拉选 new_type（Operation/Demand/Bug_fix/Internal_task/Complaint）+ POST reclassify
- 「误报关闭」→ POST dismiss-classification
成功后 invalidate 队列 query。

- [ ] **Step 3: pending_review 徽章**

在 hub 状态展示处加 `pending_review`（文案如"待确认分类"，颜色区别于现有）。位置：找渲染 hub.status 的组件（grep `pending` in frontend/src 定位）。

- [ ] **Step 4: 类型检查 + 构建**

Run（`frontend/`）: `npm run type-check && npm run build`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src
git commit -m "feat(frontend): 待确认分类队列(确认/改判/误报)+pending_review展示"
```

---

### Task 6: 全量回归 + lint

**Files:** 无（验证）

- [ ] **Step 1: 后端全量**

Run（`backend/`）: `.venv/bin/pytest -q`
Expected: 全绿（除已知 pre-existing `test_glm_client.py::test_network_error`）。

- [ ] **Step 2: lint 改动文件**

Run: `.venv/bin/ruff check app/config.py app/services/hub_issues/creator.py app/api/supervisor.py` + `.venv/bin/mypy app/services/hub_issues/creator.py app/api/supervisor.py`
Expected: clean。

- [ ] **Step 3: 前端**

Run（`frontend/`）: `npm run type-check && npm run build`
Expected: PASS。

- [ ] **Step 4: 端到端确认闸门链**

手动/脚本核对：一张 predicted_type=Bug_fix 的工单经 auto 毕业 → status=pending_review 且 SyncOutbox/Linear 无推送记录 → confirm 后 push 被调用。（可用现有集成测试或 `.venv/bin/pytest tests/unit/services/test_hub_issue_creator.py tests/unit/test_supervisor_classification_actions.py -v` 联合覆盖。）

- [ ] **Step 5: 提交（如有 lint 修复）**

```bash
git add -A && git commit -m "chore: 分类闸门全量回归+lint" || echo "无额外改动"
```

---

## Self-Review

**Spec coverage:**
- triage 基础报错降级 + plugin not found + 收紧 Bug_fix → Task 1 ✅
- require_review_before_linear 开关(默认开) → Task 2 ✅
- 自动路径 pending_review、手动路径不拦 → Task 2（只改 auto 函数）✅
- 队列端点 → Task 3 ✅
- 三动作(confirm/reclassify→Operation回炉/dismiss) → Task 4 ✅
- 改判 Operation 设 op_status=processing+op_handler=agent 回炉 drain → Task 4 test_reclassify_to_operation ✅
- 前端队列 + pending_review 展示 → Task 5 ✅
- 无迁移 → 全程无 alembic ✅

**Placeholder scan:** `_make_classified_ticket` / settings 注入 / 前端现有队列模式标注了"以文件实际为准"并给核对方法，非占位。其余测试代码可运行。

**Type consistency:** `pending_review`(hub.status)、`require_review_before_linear`、`_mark_pending_review`、三端点 body/response 模型、`apply_op_status(OP_PROCESSING, "agent")` 全程一致。pending_review 与既有 status='pending' 明确区分（Global Constraints 声明）。
