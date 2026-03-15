"""Route-level helpers and dependencies for the ops-plane API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.auth.dependencies import get_current_user, get_services
from mas_ops_api.auth.types import AuthenticatedUser
from mas_ops_api.db.models import ApprovalRequestRecord, ChatSession
from mas_ops_api.projections.repository import PortfolioQueries
from mas_ops_api.services import OpsApiServices


async def require_client_access(
    client_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> str:
    """Enforce client-scoped access and return the resolved client id."""

    if not user.can_access_client(client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )
    return client_id


async def load_incident_for_user(
    incident_id: str,
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
):
    """Load and authorize an incident record for the current user."""

    incident = await PortfolioQueries.get_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if not user.can_access_client(incident.client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )
    return incident


async def load_asset_for_user(
    asset_id: str,
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
):
    """Load and authorize an asset record for the current user."""

    asset = await PortfolioQueries.get_asset(session, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if not user.can_access_client(asset.client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )
    return asset


async def load_chat_session_for_user(
    chat_session_id: str,
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
) -> ChatSession:
    """Load and authorize a chat session for the current user."""

    chat_session = await PortfolioQueries.get_chat_session(session, chat_session_id)
    if chat_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if chat_session.client_id is not None and not user.can_access_client(
        chat_session.client_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )
    return chat_session


async def load_approval_for_user(
    approval_id: str,
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
) -> ApprovalRequestRecord:
    """Load and authorize an approval request for the current user."""

    approval = await session.get(ApprovalRequestRecord, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if not user.can_access_client(approval.client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )
    return approval


def get_command_connector_registry(
    services: OpsApiServices = Depends(get_services),
):
    """Return the per-client command connector registry."""

    return services.command_connector_registry


def get_portfolio_ingress_registry(
    services: OpsApiServices = Depends(get_services),
):
    """Return the per-client portfolio ingress registry."""

    return services.portfolio_ingress_registry


def get_stream_service(
    services: OpsApiServices = Depends(get_services),
):
    """Return the committed/live stream service."""

    return services.stream_service


def get_chat_service(
    services: OpsApiServices = Depends(get_services),
):
    """Return the chat persistence service."""

    return services.chat_service


def get_chat_execution_service(
    services: OpsApiServices = Depends(get_services),
):
    """Return the chat execution service."""

    return services.chat_execution_service


def get_config_service(
    services: OpsApiServices = Depends(get_services),
):
    """Return the config persistence service."""

    return services.config_service


__all__ = [
    "get_command_connector_registry",
    "get_chat_service",
    "get_chat_execution_service",
    "get_config_service",
    "get_portfolio_ingress_registry",
    "get_stream_service",
    "load_approval_for_user",
    "load_asset_for_user",
    "load_chat_session_for_user",
    "load_incident_for_user",
    "require_client_access",
]
