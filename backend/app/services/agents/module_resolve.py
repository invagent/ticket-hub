"""module_resolve — 产品模块归类链（决定生效 product_line_code + module）。

保证生效值必落在现有 active 目录内，绝不自建。四级回退：
  ① AI（module_classify）置信度够 → 用 AI 的 (产品线, 模块)
  ② AI 不确定 → 按工单源系统原始分类在 active 目录里精确找
  ③ 未命中 → 相似匹配（模块 name 规范化字符串比对）
  ④ 全落空 → 兜底「其他非发票云问题」(PROLINE6067)

覆盖 ticket.product_line_code/module 为规范值；源系统原值存 source_payload
["_original_catalog"] 留档；AI 原始判定写 predicted_* + 审计。不 commit（调用方管）。
永不抛（降级到兜底）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import AgentDecision, Module, ProductLine, Ticket
from app.services.agents.module_classify import classify_module

logger = get_logger(__name__)

_SKILL_NAME = "module_classify"


@dataclass(slots=True, frozen=True)
class ModuleResolveResult:
    product_line_code: str
    module: str
    source: str  # 'ai' | 'source_exact' | 'similar' | 'fallback'


def _norm(s: str | None) -> str:
    """模块名规范化：去空白、转小写，供相似匹配。"""
    return "".join((s or "").split()).lower()


def _active_module_exists(db: Session, plc: str, module: str) -> bool:
    row = db.execute(
        select(Module.id).where(
            Module.product_line_code == plc,
            Module.name == module,
            Module.is_active.is_(True),
        )
    ).first()
    return row is not None


def _active_line_exists(db: Session, plc: str) -> bool:
    row = db.execute(
        select(ProductLine.id).where(ProductLine.code == plc, ProductLine.is_active.is_(True))
    ).first()
    return row is not None


def _find_similar_module(db: Session, module: str | None) -> tuple[str, str] | None:
    """在全部 active 模块里按规范化 name 找相似（相等 / 互相包含）。命中返回 (plc, name)。"""
    if not module:
        return None
    target = _norm(module)
    if not target:
        return None
    mods = db.execute(select(Module).where(Module.is_active.is_(True))).scalars().all()
    # 优先完全相等（跨产品线的同名），再退化到包含关系
    for m in mods:
        if _norm(m.name) == target:
            return (m.product_line_code, m.name)
    for m in mods:
        nm = _norm(m.name)
        if nm and (target in nm or nm in target):
            return (m.product_line_code, m.name)
    return None


def resolve_module(
    db: Session, ticket: Ticket, *, settings: Settings | None = None
) -> ModuleResolveResult:
    """归类链主入口。覆盖 ticket 生效值 + 写 predicted_*/留档/审计。不 commit。"""
    settings = settings or get_settings()

    orig_plc = ticket.product_line_code
    orig_module = ticket.module

    # ---- ① AI 判定 ----
    ai_plc: str | None = None
    ai_module: str | None = None
    ai_conf = 0.0
    if settings.module_classify_enabled:
        ai = classify_module(db, title=ticket.title, body=ticket.body)
        if ai is not None:
            ai_plc, ai_module, ai_conf = ai.product_line_code, ai.module, ai.confidence
            ticket.predicted_product_line_code = ai_plc
            ticket.predicted_module = ai_module
            ticket.predicted_module_confidence = Decimal(f"{ai_conf:.2f}")
            ticket.module_classified_at = datetime.now(UTC)
            db.add(
                AgentDecision(
                    decision_type="classify_module",
                    subject_type="ticket",
                    subject_id=ticket.id,
                    proposal={
                        "predicted_product_line_code": ai_plc,
                        "predicted_module": ai_module,
                        "confidence": ai_conf,
                        "reason": ai.reason,
                        "model": ai.model,
                        "cost_usd": ai.cost_usd,
                        "skill": _SKILL_NAME,
                    },
                )
            )

    result: ModuleResolveResult | None = None

    # AI 够置信 + 结果在 active 目录内 → 用之
    if (
        ai_conf >= settings.module_classify_confidence
        and ai_plc
        and ai_module
        and _active_module_exists(db, ai_plc, ai_module)
    ):
        result = ModuleResolveResult(ai_plc, ai_module, "ai")

    # ---- ② 按源系统原始分类精确找 ----
    if (
        result is None
        and orig_plc
        and orig_module
        and _active_module_exists(db, orig_plc, orig_module)
    ):
        result = ModuleResolveResult(orig_plc, orig_module, "source_exact")

    # ---- ③ 相似匹配 ----
    if result is None:
        sim = _find_similar_module(db, orig_module)
        if sim is not None:
            result = ModuleResolveResult(sim[0], sim[1], "similar")

    # ---- ④ 兜底 ----
    if result is None:
        result = ModuleResolveResult(
            settings.module_fallback_product_line_code,
            settings.module_fallback_module,
            "fallback",
        )

    # 源系统原值留档（仅首次，不覆盖已有留档）
    payload = dict(ticket.source_payload or {})
    if "_original_catalog" not in payload:
        payload["_original_catalog"] = {"product_line_code": orig_plc, "module": orig_module}
        ticket.source_payload = payload

    # 覆盖生效值为规范值
    ticket.product_line_code = result.product_line_code
    ticket.module = result.module

    logger.info(
        "module_resolved",
        ticket_id=ticket.id,
        source=result.source,
        product_line_code=result.product_line_code,
        module=result.module,
        orig_plc=orig_plc,
        orig_module=orig_module,
        ai_conf=ai_conf,
    )
    return result
