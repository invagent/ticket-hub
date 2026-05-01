"""FastAPI entrypoint."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.api import admin, auth, health
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.trace import ensure_trace_id, set_trace_id


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = get_logger(__name__)
    log.info("startup", env=settings.environment, version=__version__)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ticket-hub",
        version=__version__,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def trace_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Trace-Id")
        tid = incoming or ensure_trace_id()
        set_trace_id(tid)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        get_logger(__name__).exception("unhandled", error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

    return app


app = create_app()
