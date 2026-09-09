"""统一工单主状态 `stage`（ADR-0017 D1/D2）——纯派生函数，不碰数据库。

一条工单「走到哪了」过去要同时看 4 个字段（ticket.status / hub.status / hub.op_status /
hub.linear_status）。本模块把它们确定性地折成**一个** `stage`，并定义三层格结构：

    TICKET_STAGES ⊇ HUB_STAGES ⊇ LINEAR_STAGES

后端是唯一权威（前端 processStage.ts 改为消费 API 返回的 stage，旧规则只作回落）。
派生优先级与 2026-09 之前 processStage.ts 的展示规则完全一致，保证切换时界面不变。

不 import 任何 ORM 模型（listeners.py 需要在 models.py 末尾注册，避免循环 import）；
参数按属性鸭子类型读取。
"""

from __future__ import annotations

from typing import Any, Protocol

# ---------------------------------------------------------------------------
# 取值集合（格结构）
# ---------------------------------------------------------------------------

STAGE_RECEIVED = "received"
STAGE_COMPLAINT = "complaint"
STAGE_SPLIT = "split"
STAGE_PENDING_CLASSIFY = "pending_classify"
STAGE_PENDING_DISPATCH = "pending_dispatch"
STAGE_PROCESSING = "processing"
STAGE_PENDING_ANSWER_REVIEW = "pending_answer_review"
STAGE_SUPPLEMENTING = "supplementing"
STAGE_PENDING_PUSH = "pending_push"
STAGE_IN_DEV = "in_dev"
STAGE_DEV_REVIEW = "dev_review"
STAGE_RELEASED = "released"
STAGE_CANCELED = "canceled"
STAGE_ANSWERED = "answered"
STAGE_RESOLVED = "resolved"
STAGE_CLOSED = "closed"
STAGE_RETURNED = "returned"
STAGE_EXCEPTION = "exception"

LINEAR_STAGES: frozenset[str] = frozenset(
    {STAGE_IN_DEV, STAGE_DEV_REVIEW, STAGE_RELEASED, STAGE_CANCELED}
)

HUB_STAGES: frozenset[str] = LINEAR_STAGES | frozenset(
    {
        STAGE_PENDING_CLASSIFY,
        STAGE_PENDING_DISPATCH,
        STAGE_PROCESSING,
        STAGE_PENDING_ANSWER_REVIEW,
        STAGE_SUPPLEMENTING,
        STAGE_PENDING_PUSH,
        STAGE_ANSWERED,
        STAGE_RESOLVED,
        STAGE_CLOSED,
        STAGE_RETURNED,
        STAGE_EXCEPTION,
    }
)

TICKET_STAGES: frozenset[str] = HUB_STAGES | frozenset(
    {STAGE_RECEIVED, STAGE_COMPLAINT, STAGE_SPLIT}
)

assert LINEAR_STAGES <= HUB_STAGES <= TICKET_STAGES  # 格包含关系（模块加载即校验）

# 终态：进入后除人工干预不再自动流转（对外统计口径「已完结」）
TERMINAL_STAGES: frozenset[str] = frozenset(
    {STAGE_RESOLVED, STAGE_CLOSED, STAGE_RETURNED, STAGE_CANCELED}
)

# 等人工动作的阶段（工作台队列口径）
WAITING_HUMAN_STAGES: frozenset[str] = frozenset(
    {
        STAGE_COMPLAINT,
        STAGE_PENDING_CLASSIFY,
        STAGE_PENDING_DISPATCH,
        STAGE_PENDING_ANSWER_REVIEW,
        STAGE_PENDING_PUSH,
        STAGE_EXCEPTION,
    }
)

# 中文标签（后端唯一定义；history_labels / API stage_label / 前端 STAGE_LABEL 都从这里出）
STAGE_ZH: dict[str, str] = {
    STAGE_RECEIVED: "已接收",
    STAGE_COMPLAINT: "投诉待人工",
    STAGE_SPLIT: "已拆分",
    STAGE_PENDING_CLASSIFY: "待确认分类",
    STAGE_PENDING_DISPATCH: "待指派",
    STAGE_PROCESSING: "处理中",
    STAGE_PENDING_ANSWER_REVIEW: "答复待审核",
    STAGE_SUPPLEMENTING: "待补充资料",
    STAGE_PENDING_PUSH: "待确认转研发",
    STAGE_IN_DEV: "研发中",
    STAGE_DEV_REVIEW: "测试中",
    STAGE_RELEASED: "已发版",
    STAGE_CANCELED: "已取消",
    STAGE_ANSWERED: "已答复",
    STAGE_RESOLVED: "已解决",
    STAGE_CLOSED: "已关闭",
    STAGE_RETURNED: "已退回",
    STAGE_EXCEPTION: "处理异常",
}

# 展示语义色（与前端 StageTone 同名：pending/progress/done/closed/exception/neutral）
STAGE_TONE: dict[str, str] = {
    STAGE_RECEIVED: "neutral",
    STAGE_COMPLAINT: "exception",
    STAGE_SPLIT: "neutral",
    STAGE_PENDING_CLASSIFY: "pending",
    STAGE_PENDING_DISPATCH: "pending",
    STAGE_PROCESSING: "progress",
    STAGE_PENDING_ANSWER_REVIEW: "pending",
    STAGE_SUPPLEMENTING: "progress",
    STAGE_PENDING_PUSH: "pending",
    STAGE_IN_DEV: "progress",
    STAGE_DEV_REVIEW: "progress",
    STAGE_RELEASED: "done",
    STAGE_CANCELED: "exception",
    STAGE_ANSWERED: "done",
    STAGE_RESOLVED: "closed",
    STAGE_CLOSED: "closed",
    STAGE_RETURNED: "closed",
    STAGE_EXCEPTION: "exception",
}

assert set(STAGE_ZH) == TICKET_STAGES and set(STAGE_TONE) == TICKET_STAGES


def stage_label(stage: str | None) -> str | None:
    """stage → 中文（未知原样返回，None 保持 None）。"""
    if stage is None:
        return None
    return STAGE_ZH.get(stage, stage)


# ---------------------------------------------------------------------------
# 旧字段 → stage 的映射表
# ---------------------------------------------------------------------------

_DEV_TYPES: frozenset[str] = frozenset({"Bug_fix", "Demand"})

# hub.status 闸门/待人工态（优先于运营机与研发态）
_HUB_GATE_STAGE: dict[str, str] = {
    "pending_review": STAGE_PENDING_CLASSIFY,
    "pending_linear_review": STAGE_PENDING_PUSH,
    "pending": STAGE_PENDING_DISPATCH,
}

# Operation 运营机 op_status → stage
_OP_STAGE: dict[str, str] = {
    "processing": STAGE_PROCESSING,
    "reviewing": STAGE_PENDING_ANSWER_REVIEW,
    "supplementing": STAGE_SUPPLEMENTING,
    "answered": STAGE_ANSWERED,
    "closed": STAGE_CLOSED,
    "exception": STAGE_EXCEPTION,
    "transferred_return": STAGE_RETURNED,
}

# Linear 列名（小写）→ stage（研发类）
_LINEAR_STAGE: dict[str, str] = {
    "backlog": STAGE_IN_DEV,
    "unstarted": STAGE_IN_DEV,
    "todo": STAGE_IN_DEV,
    "started": STAGE_IN_DEV,
    "in progress": STAGE_IN_DEV,
    "in review": STAGE_DEV_REVIEW,
    "done": STAGE_RELEASED,
    "completed": STAGE_RELEASED,
    "released": STAGE_RELEASED,
    "canceled": STAGE_CANCELED,
    "cancelled": STAGE_CANCELED,
}

# hub.status 历史上漏进的 Operation 值 / 其它直译
_HUB_STATUS_DIRECT: dict[str, str] = {
    "resolved": STAGE_RESOLVED,
    "closed": STAGE_CLOSED,
    "returned": STAGE_RETURNED,
    "answered": STAGE_ANSWERED,
    "processing": STAGE_PROCESSING,
}

# 无 hub 时 ticket.status 的终态直译
_TICKET_TERMINAL: dict[str, str] = {
    "done": STAGE_RESOLVED,
    "closed": STAGE_CLOSED,
    "rejected": STAGE_CLOSED,
    "superseded": STAGE_CLOSED,
    "transferred_return": STAGE_RETURNED,
}


class _HubLike(Protocol):
    type: Any
    status: Any
    op_status: Any
    linear_status: Any


class _TicketLike(Protocol):
    status: Any
    type: Any
    predicted_type: Any
    hub_issue_id: Any


def _linear_stage(linear_status: str | None) -> str | None:
    if not linear_status:
        return None
    return _LINEAR_STAGE.get(str(linear_status).strip().lower())


def derive_hub_stage(hub: _HubLike) -> str:
    """hub 层 stage（HUB_STAGES 子集）。

    优先级：退回 > 闸门/待人工 > Operation 运营机 > hub 终态 > 研发态(Linear 细粒度) >
    Internal_task/其它粗映射。永不抛：任何未知组合回落 processing。
    """
    hub_status = getattr(hub, "status", None)
    op_status = getattr(hub, "op_status", None)
    hub_type = getattr(hub, "type", None)
    linear_status = getattr(hub, "linear_status", None)

    if hub_status == "returned" or op_status == "transferred_return":
        return STAGE_RETURNED

    gate = _HUB_GATE_STAGE.get(hub_status) if hub_status else None
    if gate is not None:
        return gate

    if op_status:
        mapped = _OP_STAGE.get(op_status)
        if mapped is not None:
            return mapped

    direct = _HUB_STATUS_DIRECT.get(hub_status) if hub_status else None
    if direct is not None:
        return direct

    if hub_type in _DEV_TYPES:
        lin = _linear_stage(linear_status)
        if lin == STAGE_CANCELED:
            return STAGE_CANCELED
        if hub_status == "released" or lin == STAGE_RELEASED:
            return STAGE_RELEASED
        if lin is not None:
            return lin
        if hub_status == "in_progress":
            return STAGE_IN_DEV
        return STAGE_PROCESSING

    if hub_status == "released":
        return STAGE_RELEASED
    return STAGE_PROCESSING


def derive_ticket_stage(ticket: _TicketLike, hub: _HubLike | None) -> str:
    """ticket 层 stage（大全集）。已毕业时以 hub 派生为准；未毕业看 ticket 自身。"""
    t_status = getattr(ticket, "status", None)

    if t_status == "transferred_return":
        return STAGE_RETURNED
    if t_status == "split" or getattr(ticket, "type", None) == "Parent":
        return STAGE_SPLIT

    if hub is None:
        if getattr(ticket, "predicted_type", None) == "Complaint":
            # 投诉只要没被人工关闭/转型毕业，就停在「投诉待人工」
            if t_status in _TICKET_TERMINAL:
                return _TICKET_TERMINAL[t_status]
            return STAGE_COMPLAINT
        terminal = _TICKET_TERMINAL.get(t_status) if t_status else None
        if terminal is not None:
            return terminal
        return STAGE_RECEIVED

    return derive_hub_stage(hub)


def explain_stage_driver(ticket: _TicketLike | None, hub: _HubLike | None) -> str:
    """一句话说明本次 stage 由哪个旧字段驱动（写入 status_history.reason 便于对账）。"""
    parts: list[str] = []
    if ticket is not None:
        parts.append(f"ticket.status={getattr(ticket, 'status', None)}")
    if hub is not None:
        parts.append(f"hub.status={getattr(hub, 'status', None)}")
        if getattr(hub, "op_status", None):
            parts.append(f"op_status={hub.op_status}")
        if getattr(hub, "linear_status", None):
            parts.append(f"linear_status={hub.linear_status}")
    return ", ".join(parts)
