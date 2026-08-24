"""catalog_upsert.py — idempotent upsert of ProductLine + Module rows.

Called by every ingester before ticket creation so that a ticket arriving
with an unknown product_line_code or module never fails on FK constraints.

Uses INSERT ... ON CONFLICT DO NOTHING (PostgreSQL dialect) — safe under
concurrent requests. Does NOT commit; the caller's transaction covers it.
Must call db.flush() before returning so the FK on tickets.product_line_code
is satisfied when the ticket row is inserted.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Module, ProductLine

logger = get_logger(__name__)


def _line_exists(db: Session, code: str) -> bool:
    return db.execute(select(ProductLine.id).where(ProductLine.code == code)).first() is not None


def safe_product_line_code(db: Session, code: str | None) -> str | None:
    """入库时决定 ticket.product_line_code 落什么。

    module_classify_enabled 开时（禁止自建）：源系统串不在目录 → 返回 None，
    交归类链填规范值（避免脏产品线 + FK 违反）；已在目录则原样。
    开关关时：维持旧行为，原样返回（catalog_upsert 会把它自建）。
    """
    from app.config import get_settings

    if not code:
        return None
    if not get_settings().module_classify_enabled:
        return code
    return code if _line_exists(db, code) else None


def upsert_catalog(
    db: Session,
    *,
    product_line_code: str | None,
    module: str | None,
    product_line_name: str | None = None,
) -> None:
    """Ensure product_line and module rows exist. No-op if inputs are None/empty.

    module_classify_enabled 开时**禁止自建**（存在性交归类链保证，绝不污染目录）；
    直接返回不建任何行。开关关时维持原自建行为（向后兼容）。
    """
    from app.config import get_settings

    if get_settings().module_classify_enabled:
        return  # 禁止自建：归类链保证生效值落在现有 active 目录内
    if not product_line_code:
        return

    stmt_pl = (
        pg_insert(ProductLine)
        .values(
            code=product_line_code,
            name=product_line_name or product_line_code,
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )
    result = cast("CursorResult[Any]", db.execute(stmt_pl))
    if result.rowcount:
        logger.info("catalog_upsert_product_line_created", code=product_line_code)

    if not module:
        db.flush()
        return

    stmt_mod = (
        pg_insert(Module)
        .values(product_line_code=product_line_code, name=module, is_active=True)
        .on_conflict_do_nothing(constraint="uq_modules_pl_name")
    )
    result = cast("CursorResult[Any]", db.execute(stmt_mod))
    if result.rowcount:
        logger.info(
            "catalog_upsert_module_created",
            product_line_code=product_line_code,
            module=module,
        )

    db.flush()
