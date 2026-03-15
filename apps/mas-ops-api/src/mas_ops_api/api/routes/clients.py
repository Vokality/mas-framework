"""Client and portfolio routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.api.dependencies import require_client_access
from mas_ops_api.api.schemas import (
    ActivityEventResponse,
    AssetResponse,
    ClientSummaryResponse,
    IncidentResponse,
)
from mas_ops_api.auth.dependencies import get_current_user, get_db_session
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.projections.repository import PortfolioQueries


router = APIRouter(tags=["clients"])


@router.get("/clients", response_model=list[ClientSummaryResponse])
async def list_clients(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ClientSummaryResponse]:
    """List authorized clients for the current user."""

    rows = await PortfolioQueries.list_clients(
        session,
        allowed_client_ids=current_user.allowed_client_ids,
        admin=current_user.role is UserRole.ADMIN,
    )
    return [ClientSummaryResponse.model_validate(row) for row in rows]


@router.get("/clients/{client_id}", response_model=ClientSummaryResponse)
async def get_client(
    client_id: str = Depends(require_client_access),
    session: AsyncSession = Depends(get_db_session),
) -> ClientSummaryResponse:
    """Fetch one authorized client summary."""

    row = await PortfolioQueries.get_client(session, client_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return ClientSummaryResponse.model_validate(row)


@router.get("/clients/{client_id}/incidents", response_model=list[IncidentResponse])
async def list_client_incidents(
    client_id: str = Depends(require_client_access),
    session: AsyncSession = Depends(get_db_session),
) -> list[IncidentResponse]:
    """List incidents for one authorized client."""

    rows = await PortfolioQueries.list_incidents_for_client(session, client_id)
    return [IncidentResponse.model_validate(row) for row in rows]


@router.get("/clients/{client_id}/assets", response_model=list[AssetResponse])
async def list_client_assets(
    client_id: str = Depends(require_client_access),
    session: AsyncSession = Depends(get_db_session),
) -> list[AssetResponse]:
    """List assets for one authorized client."""

    rows = await PortfolioQueries.list_assets_for_client(session, client_id)
    return [AssetResponse.model_validate(row) for row in rows]


@router.get("/clients/{client_id}/activity", response_model=list[ActivityEventResponse])
async def list_client_activity(
    client_id: str = Depends(require_client_access),
    session: AsyncSession = Depends(get_db_session),
) -> list[ActivityEventResponse]:
    """List the latest visibility activity for one authorized client."""

    rows = await PortfolioQueries.list_activity_for_client(session, client_id)
    return [ActivityEventResponse.model_validate(row) for row in rows]


__all__ = ["router"]
