"""Bidirectional gRPC transport, delivery dispatch, and shutdown draining."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from mas_core import SpanKind, get_telemetry
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)
from opentelemetry.context import Context

from ._core import AgentCore, AgentMessage
from .config import HANDLER_DRAIN_TIMEOUT, OUTGOING_DRAIN_TIMEOUT

logger = logging.getLogger(__name__)


class TransportMixin(AgentCore):
    """Transport stream plus ACK/NACK lifecycle for :class:`Agent`."""

    async def wait_transport_ready(self, timeout: float | None = None) -> None:
        """Wait until the server transport is ready."""
        if timeout is None:
            await self._transport_ready.wait()
        else:
            await asyncio.wait_for(self._transport_ready.wait(), timeout)

    async def _transport_loop(self) -> None:
        """Stream client events and handle server deliveries."""
        stub = self._require_stub()
        telemetry = get_telemetry()

        async def outgoing_iter() -> AsyncIterator[mas_pb2.ClientEvent]:
            """Yield outbound events from the client queue."""
            while True:
                event = await self._outgoing.get()
                yield event

        await self._outgoing.put(
            mas_pb2.ClientEvent(hello=mas_pb2.Hello(instance_id=self.instance_id))
        )

        call = stub.Transport(outgoing_iter(), metadata=telemetry.grpc_metadata())

        with telemetry.start_span(
            "mas.agent.transport_loop",
            kind=SpanKind.CLIENT,
            attributes={"mas.agent_id": self.id, "mas.instance_id": self.instance_id},
        ) as span:
            try:
                async for event in call:
                    if event.HasField("welcome"):
                        self._transport_ready.set()
                        continue

                    if event.HasField("delivery"):
                        await self._handle_delivery(event.delivery)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "Transport loop failed",
                    exc_info=exc,
                    extra={"agent_id": self.id, "instance_id": self.instance_id},
                )

    async def _handle_delivery(self, delivery: mas_pb2.Delivery) -> None:
        """Validate and dispatch a delivery message."""
        telemetry = get_telemetry()
        try:
            msg = AgentMessage.model_validate_json(delivery.envelope_json)
        except Exception as exc:
            await self._send_nack(
                delivery.delivery_id,
                reason=f"invalid_envelope:{type(exc).__name__}",
                retryable=False,
            )
            return

        # Replies resolve pending requests immediately, but only when the reply
        # actually comes from the agent the request was sent to (see
        # _reply_authorized). A spoofed reply leaves the pending request in place
        # so the legitimate replier can still answer it.
        if msg.meta.is_reply and msg.meta.correlation_id:
            pending = self._pending_requests.get(msg.meta.correlation_id)
            if pending is not None:
                if self._reply_authorized(
                    msg, pending.target_id, source="live_delivery"
                ):
                    self._pending_requests.pop(msg.meta.correlation_id, None)
                    if not pending.future.done():
                        pending.future.set_result(msg)
            else:
                # No request registered yet: stash it (validated against the
                # target once request() picks it up). Bounded + TTL'd so late
                # replies for already-finished requests cannot accumulate.
                self._early_replies[msg.meta.correlation_id] = msg
            await self._send_ack(delivery.delivery_id)
            return

        parent_context = telemetry.extract_message_meta_context(msg.meta)
        task = asyncio.create_task(
            self._handle_message_and_ack(
                delivery.delivery_id,
                msg,
                parent_context=parent_context,
            )
        )
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _handle_message_and_ack(
        self,
        delivery_id: str,
        msg: AgentMessage,
        *,
        parent_context: Context | None,
    ) -> None:
        """Run handlers and ACK/NACK as needed."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.handle_message",
            kind=SpanKind.CONSUMER,
            context=parent_context,
            attributes={
                "mas.agent_id": self.id,
                "mas.message_id": msg.message_id,
                "mas.sender_id": msg.sender_id,
                "mas.message_type": msg.message_type,
            },
        ) as span:
            try:
                dispatched = await self._dispatch_typed(msg)
                if not dispatched:
                    await self.on_message(msg)
                await self._send_ack(delivery_id)
            except Exception as exc:
                span.record_exception(exc)
                logger.error(
                    "Failed to handle message",
                    exc_info=exc,
                    extra={
                        "agent_id": self.id,
                        "instance_id": self.instance_id,
                        "message_id": msg.message_id,
                        "sender_id": msg.sender_id,
                    },
                )
                await self._send_nack(
                    delivery_id,
                    reason=f"handler_error:{type(exc).__name__}",
                    retryable=False,
                )

    async def _send_ack(self, delivery_id: str) -> None:
        """Send an ACK for a delivery."""
        with contextlib.suppress(Exception):
            await self._outgoing.put(
                mas_pb2.ClientEvent(ack=mas_pb2.Ack(delivery_id=delivery_id))
            )

    async def _send_nack(
        self, delivery_id: str, *, reason: str, retryable: bool
    ) -> None:
        """Send a NACK for a delivery."""
        with contextlib.suppress(Exception):
            await self._outgoing.put(
                mas_pb2.ClientEvent(
                    nack=mas_pb2.Nack(
                        delivery_id=delivery_id,
                        reason=reason,
                        retryable=retryable,
                    )
                )
            )

    async def _drain_handler_tasks(
        self, timeout: float = HANDLER_DRAIN_TIMEOUT
    ) -> None:
        """Wait for in-flight handlers to finish, cancelling any stragglers.

        Letting handlers run to completion lets their ACK/NACK reach the
        outgoing queue before the transport is cancelled. The transport loop is
        still consuming the queue here, so a handler blocked on a full queue can
        still make progress; stragglers past the budget are cancelled.
        """
        if not self._handler_tasks:
            return
        pending = set(self._handler_tasks)
        done, _still_running = await asyncio.wait(pending, timeout=timeout)
        for task in done:
            self._handler_tasks.discard(task)

    async def _drain_outgoing(self, timeout: float = OUTGOING_DRAIN_TIMEOUT) -> None:
        """Best-effort flush of queued ACK/NACK events to the transport.

        The transport loop is still consuming from the queue, so waiting for it
        to empty lets pending acknowledgements reach the server before the
        stream is cancelled. This narrows the redelivery window; it is not a
        delivery guarantee. No-op if the transport is not running.
        """
        transport = self._transport_task
        if transport is None or transport.done():
            return
        try:
            async with asyncio.timeout(timeout):
                while not self._outgoing.empty():
                    if transport.done():
                        return
                    await asyncio.sleep(0.01)
        except TimeoutError:
            logger.warning(
                "Outgoing queue not drained before shutdown",
                extra={
                    "agent_id": self.id,
                    "instance_id": self.instance_id,
                    "pending_events": self._outgoing.qsize(),
                },
            )

    async def _cancel_handler_tasks(self) -> None:
        """Hard-cancel any handler tasks still pending after the transport stops.

        Once the transport is cancelled no more deliveries can appear, so cancel
        whatever remains rather than orphaning it. Messages owned by these
        handlers have no ACK/NACK and remain recoverable through server-side
        Redis reclaim.
        """
        if not self._handler_tasks:
            return
        pending = list(self._handler_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._handler_tasks.clear()
