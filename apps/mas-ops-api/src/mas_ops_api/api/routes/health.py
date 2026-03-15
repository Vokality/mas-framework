"""Health routes for container and process liveness checks."""

from __future__ import annotations

from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Return a minimal liveness payload for local orchestration."""

    return {"status": "ok"}


__all__ = ["router"]
