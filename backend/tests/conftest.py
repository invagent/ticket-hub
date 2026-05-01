"""Global pytest fixtures.

Strategy (D0):
  - `monkeypatch_env` ensures get_settings() picks up sane defaults in test runs.
  - `app_client` returns a FastAPI TestClient with overridden DB dependency.
  - `mock_ksm` / `mock_feishu` / `mock_zhichi` are placeholder factories for
    `responses`-based HTTP mocks; D1 fills out the recorded fixtures.

Integration tests (`-m integration`) opt into testcontainers PG/Redis via
the `pg_session` fixture below — skipped when Docker is unavailable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import responses
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_session


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a deterministic test config — no real .env leakage."""
    overrides = {
        "ENVIRONMENT": "test",
        "JWT_SECRET": "test-secret-do-not-use-in-prod",
        "PG_DSN": "sqlite+pysqlite:///:memory:",
        "FEISHU_APP_ID": "test-app",
        "FEISHU_APP_SECRET": "test-secret",
        "WEBHOOK_ACCESS_TOKEN": "test-token",
    }
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine + schema. StaticPool shares a single connection
    across sessions so the in-memory DB persists between checkout/checkin.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(sqlite_engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(sqlite_engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def app_client(db_session) -> Iterator[TestClient]:
    from app.main import create_app

    app = create_app()

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def mock_ksm():
    """Placeholder mock for KSM HTTP calls. Fixture matures in D1."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def mock_feishu():
    """Placeholder mock for Feishu HTTP calls. Fixture matures in D1."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def mock_zhichi():
    """Placeholder mock for Zhichi HTTP calls. Fixture matures in D1."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


# ---- Optional testcontainers PG fixture (integration only) ----


@pytest.fixture(scope="session")
def _pg_container_or_skip():
    if os.environ.get("CI_NO_DOCKER") == "1":
        pytest.skip("Docker not available")
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
def pg_session(_pg_container_or_skip) -> Iterator[Session]:
    pg = _pg_container_or_skip
    dsn = pg.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
