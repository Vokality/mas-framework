"""Chat session persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ChatScope, ChatTurnState

from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ChatMessage, ChatSession, ChatTurn
from mas_ops_api.streams.service import StreamService


@dataclass(frozen=True, slots=True)
class ChatSessionCreateInput:
    """Input payload for chat-session creation."""

    scope: ChatScope
    client_id: str | None
    fabric_id: str | None
    incident_id: str | None
    created_by_user_id: str


class ChatService:
    """Persist chat sessions, turns, and messages."""

    def __init__(self, stream_service: StreamService) -> None:
        self._stream_service = stream_service

    async def create_session(
        self,
        session: AsyncSession,
        payload: ChatSessionCreateInput,
    ) -> ChatSession:
        """Create a new global or incident-scoped chat session."""

        if payload.scope is ChatScope.INCIDENT:
            if (
                not payload.client_id
                or not payload.fabric_id
                or not payload.incident_id
            ):
                raise ValueError(
                    "incident-scoped chat sessions require client_id, fabric_id, and incident_id"
                )
        else:
            if payload.incident_id is not None:
                raise ValueError("global chat sessions may not set incident_id")

        chat_session = ChatSession(
            chat_session_id=str(uuid4()),
            scope=payload.scope.value,
            client_id=payload.client_id,
            fabric_id=payload.fabric_id,
            incident_id=payload.incident_id,
            created_by_user_id=payload.created_by_user_id,
        )
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        return chat_session

    async def append_operator_message(
        self,
        session: AsyncSession,
        *,
        chat_session: ChatSession,
        actor_user_id: str,
        content: str,
    ) -> tuple[ChatTurn, ChatMessage]:
        """Persist an operator message and create the matching turn."""

        now = utc_now()
        turn = ChatTurn(
            turn_id=str(uuid4()),
            request_id=str(uuid4()),
            chat_session_id=chat_session.chat_session_id,
            actor_user_id=actor_user_id,
            state=ChatTurnState.RUNNING.value,
            submitted_at=now,
        )
        message = ChatMessage(
            message_id=str(uuid4()),
            chat_session_id=chat_session.chat_session_id,
            turn_id=turn.turn_id,
            role="operator",
            content=content,
            created_at=now,
        )
        session.add(turn)
        session.add(message)
        stream_event = self._stream_service.build_event(
            event_name="chat.delta",
            client_id=chat_session.client_id,
            incident_id=chat_session.incident_id,
            chat_session_id=chat_session.chat_session_id,
            subject_type="chat_session",
            subject_id=chat_session.chat_session_id,
            payload={
                "chat_session_id": chat_session.chat_session_id,
                "turn_id": turn.turn_id,
                "message_id": message.message_id,
                "role": message.role,
                "content": message.content,
            },
            occurred_at=now,
        )
        session.add(stream_event)
        await session.commit()
        await session.refresh(turn)
        await session.refresh(message)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return turn, message

    async def list_turns(
        self, session: AsyncSession, chat_session_id: str
    ) -> list[ChatTurn]:
        """Return chat turns for one session in submission order."""

        stmt = (
            select(ChatTurn)
            .where(ChatTurn.chat_session_id == chat_session_id)
            .order_by(ChatTurn.submitted_at.asc())
        )
        return list((await session.scalars(stmt)).all())

    async def list_messages(
        self,
        session: AsyncSession,
        chat_session_id: str,
    ) -> list[ChatMessage]:
        """Return messages for one session in creation order."""

        stmt = (
            select(ChatMessage)
            .where(ChatMessage.chat_session_id == chat_session_id)
            .order_by(ChatMessage.created_at.asc())
        )
        return list((await session.scalars(stmt)).all())


__all__ = ["ChatService", "ChatSessionCreateInput"]
