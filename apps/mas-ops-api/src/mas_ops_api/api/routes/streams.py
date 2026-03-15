"""SSE routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.api.dependencies import (
    load_chat_session_for_user,
    load_incident_for_user,
)
from mas_ops_api.auth.dependencies import get_current_user, get_db_session, get_services
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.projections.repository import PortfolioQueries
from mas_ops_api.streams.service import StreamScope
from mas_ops_api.streams.sse import build_sse_response


router = APIRouter(prefix="/streams", tags=["streams"])


@router.get("/portfolio")
async def stream_portfolio(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    services=Depends(get_services),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay_only: bool = False,
):
    """Stream authorized portfolio updates."""

    scope = StreamScope(kind="portfolio", client_ids=current_user.allowed_client_ids)
    if current_user.role is UserRole.ADMIN:
        async with services.database.session_factory() as session:
            clients = await PortfolioQueries.list_clients(
                session,
                allowed_client_ids=frozenset(),
                admin=True,
            )
        scope = StreamScope(
            kind="portfolio",
            client_ids=frozenset(client.client_id for client in clients),
        )
    return build_sse_response(
        request,
        services=services,
        scope=scope,
        last_event_id=last_event_id,
        replay_only=replay_only,
    )


@router.get("/clients/{client_id}")
async def stream_client(
    request: Request,
    client_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    services=Depends(get_services),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay_only: bool = False,
):
    """Stream updates for one authorized client."""

    if not current_user.can_access_client(client_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return build_sse_response(
        request,
        services=services,
        scope=StreamScope(kind="client", client_id=client_id),
        last_event_id=last_event_id,
        replay_only=replay_only,
    )


@router.get("/incidents/{incident_id}")
async def stream_incident(
    request: Request,
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    services=Depends(get_services),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay_only: bool = False,
):
    """Stream updates for one authorized incident."""

    await load_incident_for_user(incident_id, session=session, user=current_user)
    return build_sse_response(
        request,
        services=services,
        scope=StreamScope(kind="incident", incident_id=incident_id),
        last_event_id=last_event_id,
        replay_only=replay_only,
    )


@router.get("/chat/{chat_session_id}")
async def stream_chat(
    request: Request,
    chat_session_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    services=Depends(get_services),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay_only: bool = False,
):
    """Stream updates for one authorized chat session."""

    await load_chat_session_for_user(
        chat_session_id, session=session, user=current_user
    )
    return build_sse_response(
        request,
        services=services,
        scope=StreamScope(kind="chat", chat_session_id=chat_session_id),
        last_event_id=last_event_id,
        replay_only=replay_only,
    )


__all__ = ["router"]
