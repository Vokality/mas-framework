"""Background execution for global and incident chat turns."""

from __future__ import annotations

from mas_msp_ai import DurableTaskRunner
from mas_msp_contracts import ChatScope, OperatorChatRequest

from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.chat.portfolio_assistant import PortfolioAssistant
from mas_ops_api.chat.service import ChatService
from mas_ops_api.connectors import ConnectorRegistry
from mas_ops_api.db.models import ChatSession
from mas_ops_api.db.session import Database
from mas_ops_api.streams.service import StreamService


class ChatExecutionService:
    """Schedule and complete chat turns outside the request lifecycle."""

    def __init__(
        self,
        *,
        database: Database,
        chat_service: ChatService,
        portfolio_assistant: PortfolioAssistant,
        command_connector_registry: ConnectorRegistry,
        stream_service: StreamService,
        task_runner: DurableTaskRunner,
    ) -> None:
        self._database = database
        self._chat_service = chat_service
        self._portfolio_assistant = portfolio_assistant
        self._command_connector_registry = command_connector_registry
        self._stream_service = stream_service
        self._task_runner = task_runner

    def schedule_turn(
        self,
        *,
        chat_session: ChatSession,
        turn_id: str,
        message: str,
        user: AuthenticatedUser,
    ) -> None:
        """Schedule background execution for one persisted chat turn."""

        if chat_session.scope == ChatScope.GLOBAL.value:
            self._task_runner.schedule(
                self._run_global_turn(
                    chat_session_id=chat_session.chat_session_id,
                    turn_id=turn_id,
                    message=message,
                    allowed_client_ids=user.allowed_client_ids,
                    admin=user.role is UserRole.ADMIN,
                )
            )
            return
        self._task_runner.schedule(
            self._run_incident_turn(
                chat_session_id=chat_session.chat_session_id,
                turn_id=turn_id,
                client_id=chat_session.client_id,
                fabric_id=chat_session.fabric_id,
                incident_id=chat_session.incident_id,
                message=message,
                user=user,
            )
        )

    async def _run_global_turn(
        self,
        *,
        chat_session_id: str,
        turn_id: str,
        message: str,
        allowed_client_ids: frozenset[str],
        admin: bool,
    ) -> None:
        try:
            async with self._database.session_factory() as session:
                chat_session = await session.get(ChatSession, chat_session_id)
                if chat_session is None:
                    raise LookupError("chat session was not found")
                assistant_reply = await self._portfolio_assistant.respond(
                    session,
                    allowed_client_ids=allowed_client_ids,
                    admin=admin,
                    message=message,
                )
            async with self._database.session_factory() as session:
                chat_session = await session.get(ChatSession, chat_session_id)
                if chat_session is None:
                    raise LookupError("chat session was not found")
                await self._chat_service.complete_turn(
                    session,
                    chat_session=chat_session,
                    turn_id=turn_id,
                    assistant_content=assistant_reply,
                    stream_service=self._stream_service,
                )
        except Exception as exc:
            await self._fail_turn(
                chat_session_id=chat_session_id, turn_id=turn_id, error=exc
            )

    async def _run_incident_turn(
        self,
        *,
        chat_session_id: str,
        turn_id: str,
        client_id: str | None,
        fabric_id: str | None,
        incident_id: str | None,
        message: str,
        user: AuthenticatedUser,
    ) -> None:
        try:
            request = OperatorChatRequest(
                request_id=turn_id,
                chat_session_id=chat_session_id,
                turn_id=turn_id,
                scope=ChatScope.INCIDENT,
                actor_user_id=user.user_id,
                allowed_client_ids=sorted(user.allowed_client_ids),
                client_id=client_id,
                fabric_id=fabric_id,
                incident_id=incident_id,
                asset_ids=[],
                message=message,
            )
            if request.client_id is None:
                raise LookupError("incident chat requires a client_id")
            response = await self._command_connector_registry.get(
                request.client_id
            ).dispatch_chat_request(request=request)
            async with self._database.session_factory() as session:
                chat_session = await session.get(ChatSession, chat_session_id)
                if chat_session is None:
                    raise LookupError("chat session was not found")
                await self._chat_service.complete_turn(
                    session,
                    chat_session=chat_session,
                    turn_id=turn_id,
                    assistant_content=response.markdown_summary,
                    stream_service=self._stream_service,
                    approval_id=response.approval_id,
                )
        except Exception as exc:
            await self._fail_turn(
                chat_session_id=chat_session_id, turn_id=turn_id, error=exc
            )

    async def _fail_turn(
        self, *, chat_session_id: str, turn_id: str, error: Exception
    ) -> None:
        async with self._database.session_factory() as session:
            chat_session = await session.get(ChatSession, chat_session_id)
            if chat_session is None:
                return
            await self._chat_service.fail_turn(
                session,
                chat_session=chat_session,
                turn_id=turn_id,
                message=f"Unable to complete the chat request: {error}",
                stream_service=self._stream_service,
            )


__all__ = ["ChatExecutionService"]
