"""module_classify agent — AI 从现有 active 目录里判产品线 + 模块。

两步（各一次 LLM 调用，候选都校验必落在候选内，照 hub_dedup 范式）：
  step1 定产品线：15 个 active 产品线全列 → LLM 选一个 code。
  step2 定模块：该产品线下 active 模块列表 → LLM 选一个 name。

纯判定，不写库、不覆盖生效值——由 module_resolve 编排回退 + 落库。低置信度
（< 阈值）视为「AI 不确定」，交 module_resolve 走后续回退。永不抛（降级 None）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.logging import get_logger
from app.models import Module, ProductLine

logger = get_logger(__name__)

_SKILL_NAME = "module_classify"


class _ModuleClassifyError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class ModuleClassifyResult:
    product_line_code: str | None
    module: str | None
    confidence: float
    reason: str
    cost_usd: float
    model: str


def _load_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return load_prompt(_SKILL_NAME)


def _parse(content: str, candidates: set[str]) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise _ModuleClassifyError(f"non-JSON: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise _ModuleClassifyError("not an object")
    choice = data.get("choice")
    if not isinstance(choice, str) or choice not in candidates:
        raise _ModuleClassifyError(f"choice {choice!r} not in candidates")
    return data


def _ask(
    router: LLMRouter,
    *,
    prompt: str,
    user_content: str,
    candidates: set[str],
) -> tuple[str, float, str, float, str]:
    """返回 (choice, confidence, reason, cost_usd, model)。校验 choice 落在候选内。"""
    resp = router.complete(
        [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
            LLMMessage(role="user", content="只输出 JSON。"),
        ],
        agent=_SKILL_NAME,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = _parse(resp.content, candidates)
    conf = parsed.get("confidence")
    conf_f = float(conf) if isinstance(conf, (int, float)) else 0.0
    return (
        str(parsed["choice"]),
        conf_f,
        str(parsed.get("reason") or ""),
        resp.cost_usd,
        resp.model,
    )


def classify_module(
    db: Session,
    *,
    title: str | None,
    body: str | None,
    line_hint: str | None = None,
    router: LLMRouter | None = None,
) -> ModuleClassifyResult | None:
    """AI 判产品线+模块。永不抛，失败/无候选返回 None。

    line_hint：源系统已明确产品线（如智齿 module=产品线名）时传入其 code，
    跳过 step1 直接在该产品线下选模块（省一次调用 + 更准）。None 则两步全判。
    confidence 取两步里较低的一个（整条链的信心受最弱环节约束）。
    """
    lines = db.execute(select(ProductLine).where(ProductLine.is_active.is_(True))).scalars().all()
    if not lines:
        logger.info("module_classify_no_active_lines")
        return None

    text = f"工单标题：{title or ''!r}\n问题描述：{(body or '')[:1500]!r}"
    try:
        prompt = _load_prompt()
        router = router or LLMRouter.from_settings()

        # ---- step1 定产品线（line_hint 有值则跳过，直接锁定）----
        if line_hint and any(pl.code == line_hint for pl in lines):
            pl_code, pl_conf, pl_reason, pl_cost, model = (
                line_hint,
                1.0,
                "源系统锁定产品线",
                0.0,
                "",
            )
        else:
            pl_cands = [{"code": pl.code, "name": pl.name} for pl in lines]
            pl_code, pl_conf, pl_reason, pl_cost, model = _ask(
                router,
                prompt=prompt,
                user_content=(
                    f"{text}\n\n场景 A：从以下产品线候选里选一个最匹配的 code：\n"
                    f"{json.dumps(pl_cands, ensure_ascii=False)}"
                ),
                candidates={pl.code for pl in lines},
            )

        # ---- step2 定模块（该产品线下 active 模块）----
        mods = (
            db.execute(
                select(Module).where(
                    Module.product_line_code == pl_code, Module.is_active.is_(True)
                )
            )
            .scalars()
            .all()
        )
        if not mods:
            # 产品线选了但下面没模块 → 只给产品线，模块留空交回退处理
            logger.info("module_classify_no_modules", product_line_code=pl_code)
            return ModuleClassifyResult(
                product_line_code=pl_code,
                module=None,
                confidence=pl_conf,
                reason=pl_reason,
                cost_usd=pl_cost,
                model=model,
            )

        mod_cands = [{"name": m.name} for m in mods]
        mod_name, mod_conf, mod_reason, mod_cost, model2 = _ask(
            router,
            prompt=prompt,
            user_content=(
                f"{text}\n\n场景 B：工单属于产品线「{pl_code}」，从以下模块候选里选一个"
                f"最匹配的 name：\n{json.dumps(mod_cands, ensure_ascii=False)}"
            ),
            candidates={m.name for m in mods},
        )
    except (LLMRouterError, _ModuleClassifyError) as e:
        logger.warning("module_classify_failed", error=str(e))
        return None

    return ModuleClassifyResult(
        product_line_code=pl_code,
        module=mod_name,
        confidence=min(pl_conf, mod_conf),
        reason=f"{pl_reason} / {mod_reason}",
        cost_usd=pl_cost + mod_cost,
        model=model2,
    )
