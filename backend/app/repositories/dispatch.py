"""DispatchRepository — 运营分派引擎数据访问。

匹配语义：match_* 之间 AND，列表内 OR，空列表=该维度不限。
按天计数：dispatch_log.created_at >= 今日零点（北京自然日，用 UTC 存储）。
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule
from app.services.sla.workday import BEIJING


def _today_start() -> datetime:
    """北京自然日 00:00 换算成 UTC（与 metrics/workbench.py 的 BEIJING 口径一致）。

    dispatch_log.created_at 存 UTC，故返回 tz-aware UTC datetime 供比较。
    """
    now_bj = datetime.now(UTC).astimezone(BEIJING)
    midnight_bj = datetime.combine(now_bj.date(), time.min, tzinfo=BEIJING)
    return midnight_bj.astimezone(UTC)


def _match(rule_values: list[str], candidate: str | None) -> bool:
    """空列表 = 不限（命中）；非空则 candidate 必须在列表内。"""
    if not rule_values:
        return True
    return candidate is not None and candidate in rule_values


class DispatchRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_matching_rules(self, *, source: str | None, sla: str | None) -> list[DispatchRule]:
        """match_product_lines/match_modules 暂停使用（分派提前到产品线/模块判定
        之前），不再接入匹配条件，只留来源+SLA。"""
        rules = list(
            self._db.execute(
                select(DispatchRule)
                .where(DispatchRule.is_active.is_(True), DispatchRule.rule_type == "primary")
                .order_by(DispatchRule.priority.asc(), DispatchRule.id.asc())
            )
            .scalars()
            .all()
        )
        return [r for r in rules if _match(r.match_sources, source) and _match(r.match_sla, sla)]

    def get_rule(self, rule_id: int) -> DispatchRule | None:
        return self._db.get(DispatchRule, rule_id)

    def active_assignees(self, rule_id: int, tier: str = "main") -> list[DispatchAssignee]:
        return list(
            self._db.execute(
                select(DispatchAssignee)
                .where(
                    DispatchAssignee.rule_id == rule_id,
                    DispatchAssignee.tier == tier,
                    DispatchAssignee.is_active.is_(True),
                )
                .order_by(DispatchAssignee.id.asc())
            )
            .scalars()
            .all()
        )

    def today_counts(self, rule_id: int) -> dict[int, int]:
        rows = self._db.execute(
            select(DispatchLog.assignee_user_id, func.count(DispatchLog.id))
            .where(DispatchLog.rule_id == rule_id, DispatchLog.created_at >= _today_start())
            .group_by(DispatchLog.assignee_user_id)
        ).all()
        return {int(uid): int(cnt) for uid, cnt in rows}

    def get_config(self, key: str) -> str | None:
        row = self._db.get(DispatchConfig, key)
        return row.value if row else None

    def add_log(
        self,
        *,
        ticket_id: int,
        rule_id: int | None,
        assignee_user_id: int,
        tier_hit: str,
        hub_issue_id: int | None = None,
    ) -> DispatchLog:
        log = DispatchLog(
            ticket_id=ticket_id,
            hub_issue_id=hub_issue_id,
            rule_id=rule_id,
            assignee_user_id=assignee_user_id,
            tier_hit=tier_hit,
        )
        self._db.add(log)
        self._db.flush()
        return log
