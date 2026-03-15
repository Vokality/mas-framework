"""Incident detail routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.api.dependencies import load_incident_for_user
from mas_ops_api.api.schemas import ActivityEventResponse, IncidentResponse
from mas_ops_api.auth.dependencies import get_current_user, get_db_session
from mas_ops_api.auth.types import AuthenticatedUser
from mas_ops_api.projections.repository import PortfolioQueries


router = APIRouter(tags=["incidents"])


@router.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> IncidentResponse:
    """Fetch one incident detail."""

    incident = await load_incident_for_user(
        incident_id,
        session=session,
        user=current_user,
    )
    return IncidentResponse.model_validate(incident)


@router.get(
    "/incidents/{incident_id}/activity",
    response_model=list[ActivityEventResponse],
)
async def list_incident_activity(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ActivityEventResponse]:
    """Fetch activity timeline entries for one incident."""

    await load_incident_for_user(incident_id, session=session, user=current_user)
    rows = await PortfolioQueries.list_activity_for_incident(session, incident_id)
    return [ActivityEventResponse.model_validate(row) for row in rows]


__all__ = ["router"]
