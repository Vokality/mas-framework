"""Health routes for container and process liveness checks."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    """Return a minimal readiness payload for local orchestration."""

    services = getattr(request.app.state, "services", None)
    if services is None or not services.readiness.ready:
        detail = (
            "startup_in_progress"
            if services is None
            else services.readiness.detail
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )
    return {"status": "ok"}


__all__ = ["router"]
