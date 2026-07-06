"""Draft 验证器（ADR-0016 P1）— 「检测后再更新」的落地.

promote 前用**真实工单差异回放**检验 draft：取最近 N 条工单，current 与
draft 两个 system prompt 各跑一遍纯函数 classify_payload，逐条对比输出。
不落库、不影响生产；返回差异报告由主管人工判断后再 promote。

v1 仅支持 classify（迭代最频繁、有纯函数可注入 override）；其他 skill 返回
supported=False——promote 不被阻断，但前端会提示「无自动验证器，谨慎发布」。
triage（ADR-0016 P2）落地后同样走这里。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm_router import LLMRouterError
from app.core.logging import get_logger
from app.models import Ticket

logger = get_logger(__name__)

_SUPPORTED = frozenset({"classify", "triage"})
_MAX_SAMPLE = 20


@dataclass(slots=True, frozen=True)
class ValidationRow:
    ticket_id: int
    short_code: str
    title: str | None
    current_type: str | None
    current_confidence: float | None
    draft_type: str | None
    draft_confidence: float | None
    changed: bool
    error: str | None = None


@dataclass(slots=True, frozen=True)
class ValidationReport:
    supported: bool
    message: str
    sample_size: int = 0
    changed_count: int = 0
    error_count: int = 0
    rows: list[ValidationRow] = field(default_factory=list)


def validate_draft(db: Session, name: str, *, sample: int = 8) -> ValidationReport:
    """对 draft 做差异回放。LLM 同步调用 2×sample 次，调用方注意超时预算。"""
    if name not in _SUPPORTED:
        return ValidationReport(
            supported=False,
            message=f"skill {name!r} 暂无自动验证器（v1 仅 classify）——可直接发布，建议小步修改",
        )

    from app.services.skills.prompt_store import get_prompt_row

    row = get_prompt_row(db, name)
    if row is None or not row.draft_md:
        return ValidationReport(supported=True, message="没有待验证的 draft")

    sample = max(1, min(sample, _MAX_SAMPLE))
    tickets = list(
        db.execute(
            select(Ticket)
            .where(Ticket.deleted_at.is_(None), Ticket.body.is_not(None))
            .order_by(Ticket.created_at.desc())
            .limit(sample)
        )
        .scalars()
        .all()
    )
    if not tickets:
        return ValidationReport(supported=True, message="库里没有可回放的工单样本")

    # classify 与 triage 的 payload 都返回带 .type/.confidence 的结果，接口一致
    run_payload: Callable[..., Any]
    agent_error: type[Exception]
    if name == "triage":
        from app.services.agents.triage import TriageError, triage_payload

        run_payload, agent_error = triage_payload, TriageError
    else:
        from app.services.agents.classify import ClassifyError, classify_payload

        run_payload, agent_error = classify_payload, ClassifyError

    current_prompt = row.content_md
    draft_prompt = row.draft_md
    rows: list[ValidationRow] = []
    changed = errors = 0
    for t in tickets:
        cur_type = cur_conf = dft_type = dft_conf = None
        err: str | None = None
        try:
            cur = run_payload(
                title=t.title,
                body=t.body,
                product_line_code=t.product_line_code,
                module=t.module,
                system_prompt_override=current_prompt,
            )
            cur_type, cur_conf = cur.type, cur.confidence
            dft = run_payload(
                title=t.title,
                body=t.body,
                product_line_code=t.product_line_code,
                module=t.module,
                system_prompt_override=draft_prompt,
            )
            dft_type, dft_conf = dft.type, dft.confidence
        except (agent_error, LLMRouterError) as e:
            err = str(e)[:200]
            errors += 1
        is_changed = err is None and cur_type != dft_type
        if is_changed:
            changed += 1
        rows.append(
            ValidationRow(
                ticket_id=t.id,
                short_code=t.short_code,
                title=t.title,
                current_type=cur_type,
                current_confidence=cur_conf,
                draft_type=dft_type,
                draft_confidence=dft_conf,
                changed=is_changed,
                error=err,
            )
        )

    msg = f"回放 {len(rows)} 条：{changed} 条分类结果改变" + (
        f"，{errors} 条调用失败" if errors else ""
    )
    logger.info("draft_validated", name=name, sample=len(rows), changed=changed, errors=errors)
    return ValidationReport(
        supported=True,
        message=msg,
        sample_size=len(rows),
        changed_count=changed,
        error_count=errors,
        rows=rows,
    )
