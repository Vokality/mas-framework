"""gRPC servicer wiring for MAS server runtime."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, NoReturn

import grpc
import grpc.aio as grpc_aio
from mas_core import SpanKind, get_telemetry
from mas_core.telemetry.observations import broker_scope
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)
from mas_proto.runtime.v1 import (
    runtime_pb2_grpc as mas_pb2_grpc,
)
from opentelemetry.context import Context
from redis.exceptions import RedisError

from .authn import spiffe_agent_id
from .errors import RpcError
from .types import OutboundDelivery

if TYPE_CHECKING:
    from .runtime import MASServer


async def _abort_storage_error(
    context: grpc_aio.ServicerContext, *, operation: str
) -> NoReturn:
    """Expose a stable storage status while recording the failed operation."""
    get_telemetry().record_redis_error(component="rpc", operation=operation)
    await context.abort(grpc.StatusCode.UNAVAILABLE, "storage_unavailable")


def _storage_error_boundary[RequestT, ResponseT](
    handler: Callable[
        [MasGrpcServicer, RequestT, grpc_aio.ServicerContext], Awaitable[ResponseT]
    ],
) -> Callable[
    [MasGrpcServicer, RequestT, grpc_aio.ServicerContext], Awaitable[ResponseT]
]:
    """Map storage failures consistently without exposing backend details."""

    @wraps(handler)
    async def handle(
        servicer: MasGrpcServicer,
        request: RequestT,
        context: grpc_aio.ServicerContext,
    ) -> ResponseT:
        with broker_scope(servicer._server.broker_id):
            try:
                return await handler(servicer, request, context)
            except RedisError:
                await _abort_storage_error(context, operation=handle.__name__)

    return handle


class MasGrpcServicer(mas_pb2_grpc.RuntimeServiceServicer):
    """gRPC servicer adapter for MASServer operations."""

    def __init__(self, server: MASServer):
        """Initialize servicer with MASServer runtime."""
        self._server = server

    async def _agent_id_or_abort(self, context: grpc_aio.ServicerContext) -> str | None:
        """Resolve agent id or abort the RPC."""
        try:
            return await spiffe_agent_id(context, tls=self._server._settings.tls)
        except RpcError as exc:
            try:
                await self._server.audit_authentication_denied(exc.message)
            except RedisError:
                await _abort_storage_error(context, operation="authentication_audit")
            await context.abort(exc.status, exc.message)
            return None

    def _rpc_context(self, context: grpc_aio.ServicerContext) -> Context | None:
        """Extract tracing context from inbound gRPC metadata."""
        raw_metadata = context.invocation_metadata()
        metadata: list[tuple[str, str | bytes]] | None
        metadata = None if raw_metadata is None else list(raw_metadata)
        return get_telemetry().extract_grpc_context(metadata)

    async def Transport(
        self,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        context: grpc_aio.ServicerContext,
    ) -> AsyncIterator[mas_pb2.ServerEvent]:
        """Handle bidirectional transport stream."""
        telemetry = get_telemetry()
        with (
            broker_scope(self._server.broker_id),
            telemetry.start_span(
                "mas.rpc.transport",
                kind=SpanKind.SERVER,
                context=self._rpc_context(context),
                attributes={"rpc.system": "grpc", "rpc.method": "Transport"},
            ) as span,
        ):
            agent_id = await self._agent_id_or_abort(context)
            if agent_id is None:
                return
            span.set_attribute("mas.agent_id", agent_id)

            try:
                first = await anext(request_iterator)
            except StopAsyncIteration:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "missing_hello")
                return

            if not first.HasField("hello"):
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "expected_hello")
                return

            instance_id = first.hello.instance_id
            span.set_attribute("mas.instance_id", instance_id)
            try:
                session = await self._server.connect_session(
                    agent_id=agent_id,
                    instance_id=instance_id,
                )
            except RpcError as exc:
                span.record_exception(exc)
                await context.abort(exc.status, exc.message)
                return
            except RedisError as exc:
                span.record_exception(exc)
                await _abort_storage_error(context, operation="Transport.connect")

            inbound_task = asyncio.create_task(
                self._consume_client_events(
                    request_iterator=request_iterator,
                    agent_id=agent_id,
                    instance_id=instance_id,
                    context=context,
                )
            )

            outbound_task: asyncio.Task[OutboundDelivery] | None = None
            try:
                yield mas_pb2.ServerEvent(
                    welcome=mas_pb2.Welcome(agent_id=agent_id, instance_id=instance_id)
                )
                while True:
                    if inbound_task.done():
                        await inbound_task
                        return
                    if session.task.done():
                        await session.task
                        await context.abort(
                            grpc.StatusCode.UNAVAILABLE, "delivery_worker_stopped"
                        )
                        return
                    try:
                        event = session.outbound.get_nowait()
                    except asyncio.QueueEmpty:
                        outbound_task = asyncio.create_task(session.outbound.get())
                        await asyncio.wait(
                            (outbound_task, inbound_task, session.task),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if inbound_task.done():
                            await inbound_task
                            return
                        if session.task.done():
                            await session.task
                            await context.abort(
                                grpc.StatusCode.UNAVAILABLE, "delivery_worker_stopped"
                            )
                            return
                        event = await outbound_task
                        outbound_task = None
                    session.capacity_changed.set()
                    with telemetry.start_span(
                        "mas.server.transport.write",
                        kind=SpanKind.PRODUCER,
                        context=event.parent,
                        attributes={
                            "mas.agent_id": agent_id,
                            "mas.instance_id": instance_id,
                            "mas.delivery_id": event.delivery.delivery_id,
                            "mas.outbound_queue_size": session.outbound.qsize(),
                            "mas.inflight_count": len(session.inflight),
                        },
                    ) as outbound_span:
                        if event.message_id is not None:
                            outbound_span.set_attribute(
                                "mas.message_id", event.message_id
                            )
                        pending = session.inflight.get(event.delivery.delivery_id)
                        if pending is not None:
                            outbound_span.set_attribute(
                                "mas.delivery.queue_age_ms",
                                max(0, (time.time() - pending.received_at) * 1000),
                            )
                        if await self._agent_id_or_abort(context) is None:
                            return
                        if not session.lease.live:
                            await context.abort(
                                grpc.StatusCode.UNAVAILABLE, "session_lease_lost"
                            )
                            return
                        yield mas_pb2.ServerEvent(delivery=event.delivery)
            except RpcError as exc:
                span.record_exception(exc)
                await context.abort(exc.status, exc.message)
            except asyncio.CancelledError:
                pass
            except RedisError as exc:
                span.record_exception(exc)
                await _abort_storage_error(context, operation="Transport")
            except Exception as exc:
                span.record_exception(exc)
                await context.abort(grpc.StatusCode.UNAVAILABLE, "transport_failed")
            finally:
                if outbound_task is not None:
                    outbound_task.cancel()
                    await asyncio.gather(outbound_task, return_exceptions=True)
                inbound_task.cancel()
                await asyncio.gather(inbound_task, return_exceptions=True)
                try:
                    await self._server.disconnect_session(
                        agent_id=agent_id,
                        instance_id=instance_id,
                    )
                except RedisError as exc:
                    span.record_exception(exc)
                    if context.done():
                        telemetry.record_redis_error(
                            component="rpc", operation="Transport.disconnect"
                        )
                    else:
                        await _abort_storage_error(
                            context, operation="Transport.disconnect"
                        )

    @_storage_error_boundary
    async def Send(
        self,
        request: mas_pb2.SendRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.SendResponse:
        """Handle one-way send requests."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.send",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "Send"},
        ) as span:
            sender_id = await self._agent_id_or_abort(context)
            if sender_id is None:
                return mas_pb2.SendResponse()
            span.set_attribute("mas.sender_id", sender_id)
            span.set_attribute("mas.target_id", request.target_id)
            span.set_attribute("mas.message_type", request.message_type)
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
                span.record_exception(exc)
                await context.abort(exc.status, exc.message)
                return mas_pb2.SendResponse()

    @_storage_error_boundary
    async def Request(
        self,
        request: mas_pb2.RequestRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.RequestResponse:
        """Handle request-response messages."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.request",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "Request"},
        ) as span:
            sender_id = await self._agent_id_or_abort(context)
            if sender_id is None:
                return mas_pb2.RequestResponse()
            span.set_attribute("mas.sender_id", sender_id)
            span.set_attribute("mas.target_id", request.target_id)
            span.set_attribute("mas.message_type", request.message_type)
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
                span.record_exception(exc)
                await context.abort(exc.status, exc.message)
                return mas_pb2.RequestResponse()

    @_storage_error_boundary
    async def Reply(
        self,
        request: mas_pb2.ReplyRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.ReplyResponse:
        """Handle replies to pending requests."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.reply",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "Reply"},
        ) as span:
            sender_id = await self._agent_id_or_abort(context)
            if sender_id is None:
                return mas_pb2.ReplyResponse()
            span.set_attribute("mas.sender_id", sender_id)
            span.set_attribute("mas.message_type", request.message_type)
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
                span.record_exception(exc)
                await context.abort(exc.status, exc.message)
                return mas_pb2.ReplyResponse()

    @_storage_error_boundary
    async def Discover(
        self,
        request: mas_pb2.DiscoverRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.DiscoverResponse:
        """Handle discovery requests."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.discover",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "Discover"},
        ) as span:
            agent_id = await self._agent_id_or_abort(context)
            if agent_id is None:
                return mas_pb2.DiscoverResponse()
            span.set_attribute("mas.agent_id", agent_id)
            try:
                records = await self._server.discover(
                    agent_id=agent_id,
                    capabilities=list(request.capabilities),
                )
            except RpcError as exc:
                span.record_exception(exc)
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

    @_storage_error_boundary
    async def GetState(
        self,
        request: mas_pb2.GetStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.GetStateResponse:
        """Return persisted state for the caller."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.get_state",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "GetState"},
        ):
            agent_id = await self._agent_id_or_abort(context)
            if agent_id is None:
                return mas_pb2.GetStateResponse()
            state = await self._server.get_state_snapshot(agent_id=agent_id)
            return mas_pb2.GetStateResponse(state=state.fields, revision=state.revision)

    @_storage_error_boundary
    async def UpdateState(
        self,
        request: mas_pb2.UpdateStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.UpdateStateResponse:
        """Update persisted state for the caller."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.update_state",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "UpdateState"},
        ):
            agent_id = await self._agent_id_or_abort(context)
            if agent_id is None:
                return mas_pb2.UpdateStateResponse()
            if not request.HasField("expected_revision"):
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "state_revision_required"
                )
            try:
                revision = await self._server.update_state(
                    agent_id=agent_id,
                    updates=dict(request.updates),
                    expected_revision=request.expected_revision,
                )
            except RpcError as exc:
                await context.abort(exc.status, exc.message)
                return mas_pb2.UpdateStateResponse()
            return mas_pb2.UpdateStateResponse(revision=revision)

    @_storage_error_boundary
    async def ResetState(
        self,
        request: mas_pb2.ResetStateRequest,
        context: grpc_aio.ServicerContext,
    ) -> mas_pb2.ResetStateResponse:
        """Reset persisted state for the caller."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.rpc.reset_state",
            kind=SpanKind.SERVER,
            context=self._rpc_context(context),
            attributes={"rpc.system": "grpc", "rpc.method": "ResetState"},
        ):
            agent_id = await self._agent_id_or_abort(context)
            if agent_id is None:
                return mas_pb2.ResetStateResponse()
            if not request.HasField("expected_revision"):
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "state_revision_required"
                )
            try:
                revision = await self._server.reset_state(
                    agent_id=agent_id, expected_revision=request.expected_revision
                )
            except RpcError as exc:
                await context.abort(exc.status, exc.message)
                return mas_pb2.ResetStateResponse()
            return mas_pb2.ResetStateResponse(revision=revision)

    async def _consume_client_events(
        self,
        *,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        agent_id: str,
        instance_id: str,
        context: grpc_aio.ServicerContext,
    ) -> None:
        """Bound concurrent ACK storage work and retain ordered NACK handling."""
        workers = min(32, self._server._settings.max_in_flight)
        acknowledgements: asyncio.Queue[str | None] = asyncio.Queue(workers)

        async def acknowledge() -> None:
            while True:
                delivery_id = await acknowledgements.get()
                try:
                    if delivery_id is None:
                        return
                    await self._server.handle_ack(
                        agent_id=agent_id,
                        instance_id=instance_id,
                        delivery_id=delivery_id,
                    )
                finally:
                    acknowledgements.task_done()

        try:
            async with asyncio.TaskGroup() as tasks:
                for _ in range(workers):
                    tasks.create_task(acknowledge())
                async for event in request_iterator:
                    if await self._agent_id_or_abort(context) is None:
                        break
                    if event.HasField("ack"):
                        await acknowledgements.put(event.ack.delivery_id)
                    elif event.HasField("nack"):
                        await acknowledgements.join()
                        await self._server.handle_nack(
                            agent_id=agent_id,
                            instance_id=instance_id,
                            delivery_id=event.nack.delivery_id,
                            reason=event.nack.reason,
                            retryable=event.nack.retryable,
                        )
                    else:
                        raise RpcError(
                            grpc.StatusCode.INVALID_ARGUMENT, "expected_ack_or_nack"
                        )
                for _ in range(workers):
                    await acknowledgements.put(None)
        except ExceptionGroup as errors:
            storage = errors.subgroup(RedisError)
            if storage is not None:
                raise RedisError("storage_unavailable") from storage
            pending: list[BaseException] = list(errors.exceptions)
            while pending:
                error = pending.pop()
                if isinstance(error, BaseExceptionGroup):
                    pending.extend(error.exceptions)
                elif isinstance(error, RpcError):
                    raise error from errors
            raise
