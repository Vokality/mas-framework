"""Chat session persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import CHAT_TURN_STATE_MACHINE, ChatScope, ChatTurnState

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

    async def complete_turn(
        self,
        session: AsyncSession,
        *,
        chat_session: ChatSession,
        turn_id: str,
        assistant_content: str,
        stream_service: StreamService,
        approval_id: str | None = None,
    ) -> ChatTurn:
        """Persist the assistant response and complete the matching turn."""

        return await self._finalize_turn(
            session,
            chat_session=chat_session,
            turn_id=turn_id,
            assistant_content=assistant_content,
            stream_service=stream_service,
            target_state=ChatTurnState.COMPLETED,
            approval_id=approval_id,
        )

    async def fail_turn(
        self,
        session: AsyncSession,
        *,
        chat_session: ChatSession,
        turn_id: str,
        message: str,
        stream_service: StreamService,
    ) -> ChatTurn:
        """Persist a failure message and mark the turn as failed."""

        return await self._finalize_turn(
            session,
            chat_session=chat_session,
            turn_id=turn_id,
            assistant_content=message,
            stream_service=stream_service,
            target_state=ChatTurnState.FAILED,
            approval_id=None,
        )

    async def _finalize_turn(
        self,
        session: AsyncSession,
        *,
        chat_session: ChatSession,
        turn_id: str,
        assistant_content: str,
        stream_service: StreamService,
        target_state: ChatTurnState,
        approval_id: str | None,
    ) -> ChatTurn:
        turn = await session.get(ChatTurn, turn_id)
        if turn is None:
            raise LookupError(f"chat turn {turn_id!r} was not found")
        current_state = ChatTurnState(turn.state)
        if current_state is not target_state:
            CHAT_TURN_STATE_MACHINE.require_transition(current_state, target_state)
        now = utc_now()
        turn.state = target_state.value
        turn.completed_at = now
        turn.approval_id = approval_id
        message = ChatMessage(
            message_id=str(uuid4()),
            chat_session_id=chat_session.chat_session_id,
            turn_id=turn.turn_id,
            role="assistant",
            content=assistant_content,
            created_at=now,
        )
        delta_event = stream_service.build_event(
            event_name="chat.delta",
            client_id=chat_session.client_id,
            incident_id=chat_session.incident_id,
            chat_session_id=chat_session.chat_session_id,
            subject_type="chat_session",
            subject_id=chat_session.chat_session_id,
            payload={
                "chat_session_id": chat_session.chat_session_id,
                "turn_id": turn.turn_id,
                "role": message.role,
                "content": message.content,
            },
            occurred_at=now,
        )
        completed_event = stream_service.build_event(
            event_name="chat.completed",
            client_id=chat_session.client_id,
            incident_id=chat_session.incident_id,
            chat_session_id=chat_session.chat_session_id,
            subject_type="chat_turn",
            subject_id=turn.turn_id,
            payload={
                "chat_session_id": chat_session.chat_session_id,
                "turn_id": turn.turn_id,
                "state": turn.state,
                "approval_id": approval_id,
            },
            occurred_at=now,
        )
        session.add(message)
        session.add(delta_event)
        session.add(completed_event)
        await session.flush()
        await session.commit()
        await stream_service.publish(delta_event)
        await stream_service.publish(completed_event)
        return turn


__all__ = ["ChatService", "ChatSessionCreateInput"]
