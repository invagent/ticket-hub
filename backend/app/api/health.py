"""Health probe."""

from fastapi import APIRouter

from app import __version__

router = APIRouter()


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
