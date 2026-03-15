"""Asset detail routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.api.dependencies import load_asset_for_user
from mas_ops_api.api.schemas import ActivityEventResponse, AssetResponse
from mas_ops_api.auth.dependencies import get_current_user, get_db_session
from mas_ops_api.auth.types import AuthenticatedUser
from mas_ops_api.projections.repository import PortfolioQueries


router = APIRouter(tags=["assets"])


@router.get("/assets/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AssetResponse:
    """Fetch one asset detail."""

    asset = await load_asset_for_user(asset_id, session=session, user=current_user)
    return AssetResponse.model_validate(asset)


@router.get("/assets/{asset_id}/activity", response_model=list[ActivityEventResponse])
async def list_asset_activity(
    asset_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ActivityEventResponse]:
    """Fetch one asset's projected visibility activity."""

    asset = await load_asset_for_user(asset_id, session=session, user=current_user)
    rows = await PortfolioQueries.list_activity_for_asset(session, asset.asset_id)
    return [ActivityEventResponse.model_validate(row) for row in rows]


__all__ = ["router"]
