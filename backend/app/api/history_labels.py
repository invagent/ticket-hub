"""工单时间轴（处理节点）文本人性化 —— 读取层翻译，覆盖历史存量脏数据。

status_history 的 changed_by / reason 是写入时拼死的字符串，历史行含英文类型枚举
（Bug_fix）、裸 user_id（user_id=42）、slug 前缀（user:张三 / system:reroute）。
这里在响应组装时统一翻译成中文 + 姓名，让现有工单立即可读，不依赖回填。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

# hub type 英文枚举 → 中文（后端唯一权威；前端 HUB_TYPE_LABELS 按 key 查表翻不了整句 reason）。
HUB_TYPE_ZH: dict[str, str] = {
    "Operation": "运营",
    "Bug_fix": "Bug修复",
    "Demand": "需求",
    "Internal_task": "内部任务",
    "Complaint": "投诉",
}

# ticket.status / hub.status 英文枚举 → 中文（状态流转 from→to 展示）。
STATUS_ZH: dict[str, str] = {
    "received": "已接收",
    "linked": "已关联",
    "split": "已拆分",
    "in_progress": "处理中",
    "released": "已发版",
    "resolved": "已解决",
    "closed": "已关闭",
    "done": "已完成",
    "rejected": "已驳回",
    "superseded": "被取代",
    "deleted": "已删除",
    "pending": "待人工处理",
    "pending_review": "待确认分类",
    "pending_linear_review": "待确认推送",
    "created": "已创建",
}

# changed_by 的非人类 actor 前缀 slug → 中文（system:/agent:/cascade:/op: 等）。
_ACTOR_SLUG_ZH: dict[str, str] = {
    "system:ingest": "系统入库",
    "system:reroute": "系统重新分派",
    "system:manual_assign": "人工转交",
    "agent:linear_push": "系统推送 Linear",
    "agent:linear_status_sync": "Linear 状态同步",
    "agent:hub_dedup": "AI 查重",
    "agent:triage": "AI 分类",
    "agent:classify": "AI 分类",
}


def humanize_type_tokens(text: str) -> str:
    """把自由文本里出现的英文类型枚举整词替换成中文（改判 reason 等）。"""
    for en, zh in HUB_TYPE_ZH.items():
        text = re.sub(rf"\b{en}\b", zh, text)
    return text


def humanize_status(status: str | None) -> str | None:
    """单个状态枚举 → 中文（未知原样返回）。"""
    if status is None:
        return None
    return STATUS_ZH.get(status, status)


def humanize_actor(changed_by: str | None, name_by_id: dict[int, str]) -> str:
    """changed_by slug → 人类可读。

    user:{name} / op:user:{name} → 姓名；system:/agent:/cascade: → 中文角色；
    其余原样。name_by_id 用于把嵌进 reason 的 user_id 换姓名（此处仅处理 changed_by）。
    """
    if not changed_by:
        return "—"
    cb = changed_by
    # op:user:张三 / op:agent → 去 op: 前缀后再判
    if cb.startswith("op:"):
        cb = cb[3:]
    if cb.startswith("user:"):
        return cb[5:]  # user:张三 → 张三
    if cb in _ACTOR_SLUG_ZH:
        return _ACTOR_SLUG_ZH[cb]
    # cascade:xxx / agent:xxx 未列入表的 → 取冒号后半，前缀译"系统"
    if cb.startswith(("system:", "agent:", "cascade:")):
        return "系统"
    return cb


def humanize_reason(reason: str | None, name_by_id: dict[int, str]) -> str | None:
    """reason 文本人性化：英文类型→中文 + user_id={n}→姓名。"""
    if not reason:
        return reason
    text = humanize_type_tokens(reason)

    # user_id=42 → 姓名（查不到留原样 user_id=42，不误导）
    def _sub_uid(m: re.Match[str]) -> str:
        uid = int(m.group(1))
        return name_by_id.get(uid, m.group(0))

    text = re.sub(r"user_id=(\d+)", _sub_uid, text)
    return text


_UID_RE = re.compile(r"user_id=(\d+)")


def collect_user_ids(reasons: list[str | None]) -> set[int]:
    """从一批 reason 里抠出所有 user_id={n}，供批量查姓名（避免 N+1）。"""
    ids: set[int] = set()
    for r in reasons:
        if r:
            ids.update(int(m) for m in _UID_RE.findall(r))
    return ids


def load_user_names(db: Session, user_ids: set[int]) -> dict[int, str]:
    """批量查 id→姓名。"""
    if not user_ids:
        return {}
    rows = db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
    return {r.id: r.name for r in rows}
