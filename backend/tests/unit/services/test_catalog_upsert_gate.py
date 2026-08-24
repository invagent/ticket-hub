"""catalog_upsert / safe_product_line_code 的归类开关行为。

module_classify_enabled 开时禁止自建（存在性交归类链）；关时维持旧自建行为。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Module, ProductLine
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog


@pytest.fixture(autouse=True)
def _clear_settings() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _line_count(db: Session) -> int:
    return len(db.execute(select(ProductLine.id)).all())


def _mod_count(db: Session) -> int:
    return len(db.execute(select(Module.id)).all())


def test_upsert_creates_when_disabled(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MODULE_CLASSIFY_ENABLED", "false")
    get_settings.cache_clear()
    upsert_catalog(db_session, product_line_code="新码X", module="新模块Y")
    assert _line_count(db_session) == 1
    assert _mod_count(db_session) == 1


def test_upsert_noop_when_enabled(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """开关开 → 禁止自建，一行都不建。"""
    monkeypatch.setenv("MODULE_CLASSIFY_ENABLED", "true")
    get_settings.cache_clear()
    upsert_catalog(db_session, product_line_code="新码X", module="新模块Y")
    assert _line_count(db_session) == 0
    assert _mod_count(db_session) == 0


def test_safe_plc_disabled_passthrough(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MODULE_CLASSIFY_ENABLED", "false")
    get_settings.cache_clear()
    assert safe_product_line_code(db_session, "任意码") == "任意码"


def test_safe_plc_enabled_unknown_to_none(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """开关开 + 产品线不在目录 → None（交归类链，避免 FK 违反）。"""
    monkeypatch.setenv("MODULE_CLASSIFY_ENABLED", "true")
    get_settings.cache_clear()
    assert safe_product_line_code(db_session, "不存在的码") is None


def test_safe_plc_enabled_known_kept(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MODULE_CLASSIFY_ENABLED", "true")
    get_settings.cache_clear()
    db_session.add(ProductLine(code="PROLINE1", name="x", is_active=True))
    db_session.commit()
    assert safe_product_line_code(db_session, "PROLINE1") == "PROLINE1"
