"""gRPC servicer wiring for MAS server runtime."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator

import grpc
import grpc.aio as grpc_aio

from .._proto.v1 import mas_pb2, mas_pb2_grpc
from .authn import spiffe_agent_id
from .errors import RpcError

if TYPE_CHECKING:
    from .runtime import MASServer


class MasGrpcServicer(mas_pb2_grpc.MasServiceServicer):
    """gRPC servicer adapter for MASServer operations."""

    def __init__(self, server: MASServer):
        """Initialize servicer with MASServer runtime."""
        self._server = server

    async def _agent_id_or_abort(self, context: grpc_aio.ServicerContext) -> str | None:
        """Resolve agent id or abort the RPC."""
        try:
            return spiffe_agent_id(context)
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return None

    async def Transport(
        self,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        context: grpc_aio.ServicerContext,
    ) -> AsyncIterator[mas_pb2.ServerEvent]:
        """Handle bidirectional transport stream."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return

        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "missing_hello")
            return

        if not first.HasField("hello"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "expected_hello")
            return

        instance_id = first.hello.instance_id
        try:
            session = await self._server.connect_session(
                agent_id=agent_id,
                instance_id=instance_id,
            )
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return

        inbound_task = asyncio.create_task(
            self._consume_client_events(
                request_iterator=request_iterator,
                agent_id=agent_id,
                instance_id=instance_id,
            )
        )

        yield mas_pb2.ServerEvent(
            welcome=mas_pb2.Welcome(agent_id=agent_id, instance_id=instance_id)
        )

        try:
            while True:
                event = await session.outbound.get()
                yield event
        except asyncio.CancelledError:
            pass
        finally:
            inbound_task.cancel()
            await asyncio.gather(inbound_task, return_exceptions=True)
            await self._server.disconnect_session(
                agent_id=agent_id,
                instance_id=instance_id,
            )

    async def Send(
        self,
        request: mas_pb2.SendRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.SendResponse:
        """Handle one-way send requests."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.SendResponse()
        try:
            message_id = await self._server.send_message(
                sender_id=sender_id,
                sender_instance_id=request.instance_id,
                target_id=request.target_id,
                message_type=request.message_type,
                data_json=request.data_json,
            )
            return mas_pb2.SendResponse(message_id=message_id)
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.SendResponse()

    async def Request(
        self,
        request: mas_pb2.RequestRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.RequestResponse:
        """Handle request-response messages."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.RequestResponse()
        try:
            message_id, correlation_id = await self._server.request_message(
                sender_id=sender_id,
                sender_instance_id=request.instance_id,
                target_id=request.target_id,
                message_type=request.message_type,
                data_json=request.data_json,
                timeout_ms=request.timeout_ms,
            )
            return mas_pb2.RequestResponse(
                message_id=message_id,
                correlation_id=correlation_id,
            )
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.RequestResponse()

    async def Reply(
        self,
        request: mas_pb2.ReplyRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.ReplyResponse:
        """Handle replies to pending requests."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.ReplyResponse()
        try:
            message_id = await self._server.reply_message(
                sender_id=sender_id,
                sender_instance_id=request.instance_id,
                correlation_id=request.correlation_id,
                message_type=request.message_type,
                data_json=request.data_json,
            )
            return mas_pb2.ReplyResponse(message_id=message_id)
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.ReplyResponse()

    async def Discover(
        self,
        request: mas_pb2.DiscoverRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.DiscoverResponse:
        """Handle discovery requests."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.DiscoverResponse()
        try:
            records = await self._server.discover(
                agent_id=agent_id,
                capabilities=list(request.capabilities),
            )
        except RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.DiscoverResponse()

        agents: list[mas_pb2.AgentRecord] = []
        for rec in records:
            agents.append(
                mas_pb2.AgentRecord(
                    agent_id=rec["id"],
                    capabilities=list(rec["capabilities"]),
                    metadata_json=json.dumps(rec["metadata"]),
                    status=str(rec["status"]),
                )
            )
        return mas_pb2.DiscoverResponse(agents=agents)

    async def GetState(
        self,
        request: mas_pb2.GetStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.GetStateResponse:
        """Return persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.GetStateResponse()
        state = await self._server.get_state(agent_id=agent_id)
        return mas_pb2.GetStateResponse(state=state)

    async def UpdateState(
        self,
        request: mas_pb2.UpdateStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.UpdateStateResponse:
        """Update persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.UpdateStateResponse()
        await self._server.update_state(
            agent_id=agent_id, updates=dict(request.updates)
        )
        return mas_pb2.UpdateStateResponse()

    async def ResetState(
        self,
        request: mas_pb2.ResetStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.ResetStateResponse:
        """Reset persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.ResetStateResponse()
        await self._server.reset_state(agent_id=agent_id)
        return mas_pb2.ResetStateResponse()

    async def _consume_client_events(
        self,
        *,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        agent_id: str,
        instance_id: str,
    ) -> None:
        """Consume inbound ACK/NACK events."""
        async for event in request_iterator:
            if event.HasField("ack"):
                await self._server.handle_ack(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    delivery_id=event.ack.delivery_id,
                )
            elif event.HasField("nack"):
                await self._server.handle_nack(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    delivery_id=event.nack.delivery_id,
                    reason=event.nack.reason,
                    retryable=event.nack.retryable,
                )
