"""DispatchRepository — 运营分派引擎数据访问。

匹配语义：match_* 之间 AND，列表内 OR，空列表=该维度不限。
按天计数：dispatch_log.created_at >= 今日零点（本地自然日，用 UTC 存储）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule


def _today_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _match(rule_values: list[str], candidate: str | None) -> bool:
    """空列表 = 不限（命中）；非空则 candidate 必须在列表内。"""
    if not rule_values:
        return True
    return candidate is not None and candidate in rule_values


class DispatchRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_matching_rules(
        self, *, source: str | None, product_line_code: str | None,
        module: str | None, sla: str | None,
    ) -> list[DispatchRule]:
        rules = list(
            self._db.execute(
                select(DispatchRule)
                .where(DispatchRule.is_active.is_(True), DispatchRule.rule_type == "primary")
                .order_by(DispatchRule.priority.asc(), DispatchRule.id.asc())
            ).scalars().all()
        )
        return [
            r for r in rules
            if _match(r.match_sources, source)
            and _match(r.match_product_lines, product_line_code)
            and _match(r.match_modules, module)
            and _match(r.match_sla, sla)
        ]

    def get_rule(self, rule_id: int) -> DispatchRule | None:
        return self._db.get(DispatchRule, rule_id)

    def active_assignees(self, rule_id: int, tier: str = "main") -> list[DispatchAssignee]:
        return list(
            self._db.execute(
                select(DispatchAssignee).where(
                    DispatchAssignee.rule_id == rule_id,
                    DispatchAssignee.tier == tier,
                    DispatchAssignee.is_active.is_(True),
                ).order_by(DispatchAssignee.id.asc())
            ).scalars().all()
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
        self, *, hub_issue_id: int, rule_id: int | None, assignee_user_id: int, tier_hit: str
    ) -> DispatchLog:
        log = DispatchLog(
            hub_issue_id=hub_issue_id, rule_id=rule_id,
            assignee_user_id=assignee_user_id, tier_hit=tier_hit,
        )
        self._db.add(log)
        self._db.flush()
        return log
