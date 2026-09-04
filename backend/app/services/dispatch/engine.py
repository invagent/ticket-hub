"""处理人分派引擎.

入库即分派改造后，在 ticket 入库阶段（module_resolve/triage 之前）调用，选
处理人（Operation 运营 / 研发类均可）。绝不抛异常（吞掉返回 None），不阻断
入库。count：今日未达 daily_cap 者选最少，全满→溢出规则→兜底配置。
ratio：按 alloc_value 权重，选今日「应得占比 - 实际占比」缺口最大者。
按天靠 today_counts 只查当天 dispatch_log 天然重置。

match_product_lines/match_modules 暂停使用（分派此时早于产品线/模块判定），
只按来源（+ SLA，目前调用方恒传 None）匹配。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import DispatchAssignee, DispatchRule, Ticket, User
from app.repositories.dispatch import DispatchRepository

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class DispatchResult:
    user_id: int | None
    user_name: str | None
    rule_id: int | None
    tier: str | None
    reason: str


_NONE = DispatchResult(user_id=None, user_name=None, rule_id=None, tier=None, reason="no dispatch")


def _valid_user_name(db: Session, user_id: int) -> str | None:
    u = db.get(User, user_id)
    if u is None or u.deleted_at is not None or not u.is_active:
        return None
    return u.name


def _pick_count(assignees: list[DispatchAssignee], counts: dict[int, int]) -> int | None:
    """今日未达 daily_cap 的人里选今日最少者；全满返回 None。"""
    avail = [a for a in assignees if a.daily_cap is None or counts.get(a.user_id, 0) < a.daily_cap]
    if not avail:
        return None
    return min(avail, key=lambda a: counts.get(a.user_id, 0)).user_id


def _pick_ratio(assignees: list[DispatchAssignee], counts: dict[int, int]) -> int | None:
    """按 alloc_value 权重选「应得占比 - 实际占比」缺口最大者。"""
    if not assignees:
        return None
    total_w = sum(float(a.alloc_value) for a in assignees) or 1.0
    total_n = sum(counts.get(a.user_id, 0) for a in assignees) or 0
    best_uid, best_gap = None, None
    for a in assignees:
        want = float(a.alloc_value) / total_w
        actual = (counts.get(a.user_id, 0) / total_n) if total_n else 0.0
        gap = want - actual
        if best_gap is None or gap > best_gap:
            best_gap, best_uid = gap, a.user_id
    return best_uid


def _pick_from_rule(
    db: Session, repo: DispatchRepository, rule: DispatchRule
) -> tuple[int | None, str]:
    """在单条规则的 main 层选人。返回 (user_id, tier)。全不可用 → (None, '')。"""
    assignees = [
        a
        for a in repo.active_assignees(rule.id, tier="main")
        if _valid_user_name(db, a.user_id) is not None
    ]
    if not assignees:
        return None, ""
    counts = repo.today_counts(rule.id)
    uid = (
        _pick_ratio(assignees, counts)
        if rule.dispatch_mode == "ratio"
        else _pick_count(assignees, counts)
    )
    return uid, ("main" if uid is not None else "")


def dispatch_handler(db: Session, ticket: Ticket) -> DispatchResult:
    """按来源+规则选处理人。任异常吞掉返回 _NONE。"""
    try:
        repo = DispatchRepository(db)
        rules = repo.find_matching_rules(source=ticket.source_code, sla=None)
        if not rules:
            return _NONE
        rule = rules[0]  # priority 最小

        uid, tier = _pick_from_rule(db, repo, rule)
        rule_id_hit: int | None = rule.id

        # main 全满 → 溢出规则
        if uid is None and rule.overflow_rule_id is not None:
            over = repo.get_rule(rule.overflow_rule_id)
            if over is not None and over.is_active:
                uid, _ = _pick_from_rule(db, repo, over)
                if uid is not None:
                    tier, rule_id_hit = "overflow", over.id

        # 仍无 → 兜底配置
        if uid is None:
            cfg = repo.get_config("default_operation_assignee")
            if cfg and cfg.isdigit():
                uid, tier, rule_id_hit = int(cfg), "default", None

        if uid is None:
            return _NONE
        name = _valid_user_name(db, uid)
        if name is None:
            return _NONE

        repo.add_log(ticket_id=ticket.id, rule_id=rule_id_hit, assignee_user_id=uid, tier_hit=tier)
        return DispatchResult(
            user_id=uid,
            user_name=name,
            rule_id=rule_id_hit,
            tier=tier,
            reason=f"dispatch tier={tier} rule={rule_id_hit}",
        )
    except Exception:
        logger.exception("dispatch_handler_failed", ticket_id=getattr(ticket, "id", None))
        return _NONE
