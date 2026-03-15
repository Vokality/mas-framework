"""Chat session routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ChatScope

from mas_ops_api.api.dependencies import (
    get_chat_execution_service,
    get_chat_service,
    load_chat_session_for_user,
)
from mas_ops_api.api.schemas import (
    ChatMessageCreateRequest,
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionResponse,
    ChatTurnResponse,
)
from mas_ops_api.auth.dependencies import get_current_user, get_db_session
from mas_ops_api.auth.types import AuthenticatedUser
from mas_ops_api.chat.service import ChatService, ChatSessionCreateInput
from mas_ops_api.chat import ChatExecutionService
from mas_ops_api.projections.repository import PortfolioQueries


router = APIRouter(prefix="/chat", tags=["chat"])


def _build_chat_session_response(
    chat_session,
    *,  # noqa: ANN001
    turns,
    messages,
) -> ChatSessionResponse:
    """Build the chat session response including persisted turns and messages."""

    return ChatSessionResponse(
        chat_session_id=chat_session.chat_session_id,
        scope=chat_session.scope,
        client_id=chat_session.client_id,
        fabric_id=chat_session.fabric_id,
        incident_id=chat_session.incident_id,
        created_by_user_id=chat_session.created_by_user_id,
        created_at=chat_session.created_at,
        turns=[ChatTurnResponse.model_validate(turn) for turn in turns],
        messages=[ChatMessageResponse.model_validate(message) for message in messages],
    )


@router.post(
    "/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED
)
async def create_chat_session(
    payload: ChatSessionCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    """Create a global or incident-scoped chat session."""

    if payload.scope is ChatScope.INCIDENT:
        if payload.client_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="incident-scoped chat requires client_id",
            )
        if not current_user.can_access_client(payload.client_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="forbidden",
            )
        if payload.incident_id is not None:
            incident = await PortfolioQueries.get_incident(session, payload.incident_id)
            if incident is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="not_found",
                )
            if incident.client_id != payload.client_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="incident_client_mismatch",
                )

    chat_session = await chat_service.create_session(
        session,
        ChatSessionCreateInput(
            scope=payload.scope,
            client_id=payload.client_id,
            fabric_id=payload.fabric_id,
            incident_id=payload.incident_id,
            created_by_user_id=current_user.user_id,
        ),
    )
    turns = await chat_service.list_turns(session, chat_session.chat_session_id)
    messages = await chat_service.list_messages(session, chat_session.chat_session_id)
    return _build_chat_session_response(
        chat_session,
        turns=turns,
        messages=messages,
    )


@router.get("/sessions/{chat_session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    chat_session_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    """Fetch one chat session and its persisted turns/messages."""

    chat_session = await load_chat_session_for_user(
        chat_session_id,
        session=session,
        user=current_user,
    )
    turns = await chat_service.list_turns(session, chat_session.chat_session_id)
    messages = await chat_service.list_messages(session, chat_session.chat_session_id)
    return _build_chat_session_response(
        chat_session,
        turns=turns,
        messages=messages,
    )


@router.post(
    "/sessions/{chat_session_id}/messages",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_chat_message(
    chat_session_id: str,
    payload: ChatMessageCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    chat_service: ChatService = Depends(get_chat_service),
    chat_execution_service: ChatExecutionService = Depends(get_chat_execution_service),
) -> ChatTurnResponse:
    """Append one operator message to a chat session."""

    chat_session = await load_chat_session_for_user(
        chat_session_id,
        session=session,
        user=current_user,
    )
    turn, _message = await chat_service.append_operator_message(
        session,
        chat_session=chat_session,
        actor_user_id=current_user.user_id,
        content=payload.message,
    )
    chat_execution_service.schedule_turn(
        chat_session=chat_session,
        turn_id=turn.turn_id,
        message=payload.message,
        user=current_user,
    )
    return ChatTurnResponse.model_validate(turn)


__all__ = ["router"]
