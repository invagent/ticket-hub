"""catalog_upsert.py — idempotent upsert of ProductLine + Module rows.

Called by every ingester before ticket creation so that a ticket arriving
with an unknown product_line_code or module never fails on FK constraints.

Uses INSERT ... ON CONFLICT DO NOTHING (PostgreSQL dialect) — safe under
concurrent requests. Does NOT commit; the caller's transaction covers it.
Must call db.flush() before returning so the FK on tickets.product_line_code
is satisfied when the ticket row is inserted.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Module, ProductLine

logger = get_logger(__name__)


def upsert_catalog(
    db: Session,
    *,
    product_line_code: str | None,
    module: str | None,
    product_line_name: str | None = None,
) -> None:
    """Ensure product_line and module rows exist. No-op if inputs are None/empty."""
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
    result = db.execute(stmt_pl)
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
    result = db.execute(stmt_mod)
    if result.rowcount:
        logger.info(
            "catalog_upsert_module_created",
            product_line_code=product_line_code,
            module=module,
        )

    db.flush()
