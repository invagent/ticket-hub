"""Health probes.

  GET /health           liveness — process is up (cheap; never touches DB)
  GET /health/ready     readiness — dependencies (PG, ...) are reachable; 503 if any fail

K8s convention:
  - liveness probe (kubelet) → /health
  - readiness probe (traffic gating) → /health/ready

Each check entry returns {name, ok, latency_ms, error?}. The overall status:
  - "ready"      all required checks ok
  - "degraded"   non-required checks failing (none yet; placeholder)
  - "unhealthy"  ≥1 required check failing → 503

Adding a new check: write a `_check_X(...) -> CheckResult` helper that catches
its own exceptions; append to `_run_checks`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.db import get_session

router = APIRouter()


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ---- /health/ready -------------------------------------------------------


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    latency_ms: float
    error: str | None = None
    required: bool = True


class CheckOut(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    error: str | None = None
    required: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded", "unhealthy"]
    version: str
    checks: list[CheckOut]


def _check_pg(db: Session) -> CheckResult:
    """SELECT 1 round-trip. Catches everything; never raises."""
    started = time.perf_counter()
    try:
        result = db.execute(text("SELECT 1")).scalar()
        if result != 1:
            return CheckResult(
                name="postgres",
                ok=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"unexpected scalar={result}",
            )
        return CheckResult(
            name="postgres",
            ok=True,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
    except Exception as e:
        return CheckResult(
            name="postgres",
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=f"{type(e).__name__}: {e}",
        )


def _run_checks(db: Session) -> list[CheckResult]:
    return [_check_pg(db)]


@router.get("/health/ready", response_model=ReadinessResponse, tags=["health"])
def ready(response: Response, db: Session = Depends(get_session)) -> ReadinessResponse:
    checks = _run_checks(db)
    required_failing = any(not c.ok for c in checks if c.required)
    optional_failing = any(not c.ok for c in checks if not c.required)

    if required_failing:
        status: Literal["ready", "degraded", "unhealthy"] = "unhealthy"
        response.status_code = 503
    elif optional_failing:
        status = "degraded"
    else:
        status = "ready"

    return ReadinessResponse(
        status=status,
        version=__version__,
        checks=[
            CheckOut(
                name=c.name,
                ok=c.ok,
                latency_ms=round(c.latency_ms, 2),
                error=c.error,
                required=c.required,
            )
            for c in checks
        ],
    )
