"""SQLAlchemy engine + session factory."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def init_engine(dsn: str | None = None) -> None:
    """Initialise the global engine. Idempotent.

    For SQLite (used by unit tests) we skip pool_size/max_overflow which
    aren't supported by SingletonThreadPool, and use StaticPool so a single
    in-memory connection is shared across sessions (otherwise each session
    sees an empty `:memory:` database).
    """
    global _engine, _SessionLocal
    if _engine is not None:
        return
    settings = get_settings()
    effective_dsn = dsn or settings.pg_dsn
    if effective_dsn.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        _engine = create_engine(
            effective_dsn,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    else:
        _engine = create_engine(
            effective_dsn,
            pool_size=settings.pg_pool_size,
            max_overflow=settings.pg_max_overflow,
            pool_pre_ping=True,
            future=True,
        )
    _SessionLocal = sessionmaker(_engine, autoflush=False, autocommit=False, future=True)


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_session() -> Session:
    """Open a fresh session — for callers outside the request lifecycle
    (e.g. FastAPI BackgroundTasks, Celery workers). Caller must close()."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
