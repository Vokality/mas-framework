"""Delivery workers and ACK/NACK handling."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Literal

import grpc
from mas_core import EnvelopeMessage, SpanKind, get_telemetry
from mas_core.sessions import SessionLease
from mas_gateway import CircuitBreakerModule
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from .errors import RpcError
from .routing import DeliveryCommit, MessageRouter
from .sessions import SessionManager
from .types import InflightDelivery, MASServerSettings, OutboundDelivery

logger = logging.getLogger(__name__)

_STREAM_READ_ADAPTER = TypeAdapter(
    tuple[Literal[0, 1], list[tuple[str, list[tuple[str, list[str]]]]]]
)
_READ_SCRIPT = """
if redis.call('GET', KEYS[#KEYS]) ~= ARGV[1] then return {0, {}} end
local command = {'XREADGROUP', 'GROUP', ARGV[2], ARGV[3], 'COUNT', ARGV[4], 'STREAMS'}
for i = 1, #KEYS - 1 do command[#command + 1] = KEYS[i] end
for i = 1, #KEYS - 1 do command[#command + 1] = '>' end
return {1, redis.call(unpack(command)) or {}}
"""
_CLAIM_ADAPTER = TypeAdapter(tuple[str, list[tuple[str, list[str]]], list[str]])
_CLAIM_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return nil end
return redis.call('XAUTOCLAIM', KEYS[1], ARGV[2], ARGV[3],
    ARGV[4], ARGV[5], 'COUNT', ARGV[6])
"""

_ACK_AND_DELETE_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[4] then return 0 end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 or pending[1][2] ~= ARGV[3] then
    return 0
end
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acknowledged > 0 then
    redis.call('XDEL', KEYS[1], ARGV[2])
end
return acknowledged
"""

_REQUEUE_AND_ACK_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[6] then return 0 end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 or pending[1][2] ~= ARGV[5] then
    return 0
end
redis.call('XADD', KEYS[1], '*', 'envelope', ARGV[3], 'attempt', ARGV[4])
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
redis.call('XDEL', KEYS[1], ARGV[2])
return 1
"""


class DeliveryService:
    """Run stream delivery loops and handle ACK/NACK outcomes."""

    def __init__(
        self,
        *,
        redis: Redis,
        settings: MASServerSettings,
        sessions: SessionManager,
        router: MessageRouter,
        circuit_breaker: CircuitBreakerModule | None,
    ) -> None:
        """Initialize delivery service."""
        self._redis = redis
        self._settings = settings
        self._sessions = sessions
        self._router = router
        self._circuit_breaker = circuit_breaker
        self._running = False

    def set_running(self, running: bool) -> None:
        """Enable or disable stream loops."""
        self._running = running

    def _available_capacity(
        self,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
    ) -> int:
        """Budget both unacknowledged deliveries and the transport queue."""
        capacity = self._settings.max_in_flight - len(inflight)
        if outbound.maxsize > 0:
            capacity = min(capacity, outbound.maxsize - outbound.qsize())
        return capacity

    def start_stream_task(
        self,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
    ) -> asyncio.Task[None]:
        """Create the delivery task for a session."""
        return asyncio.create_task(
            self._stream_loop(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
            )
        )

    async def handle_ack(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
    ) -> None:
        """Handle delivery ACK and update inflight state."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.delivery.handle_ack",
            kind=SpanKind.CONSUMER,
            attributes={
                "mas.agent_id": agent_id,
                "mas.instance_id": instance_id,
            },
        ):
            lease = self._sessions.lease(agent_id, instance_id)
            inflight = await self._sessions.pop_inflight(
                agent_id=agent_id,
                instance_id=instance_id,
                delivery_id=delivery_id,
            )
            if not inflight:
                return

            if await self._ack_inflight(
                inflight, consumer=inflight.consumer, lease=lease
            ):
                telemetry.record_delivery_ack()
                if self._circuit_breaker:
                    await self._circuit_breaker.record_success(agent_id)

    async def handle_nack(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
        reason: str,
        retryable: bool,
    ) -> None:
        """Handle delivery NACK and retry or DLQ."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.server.delivery.handle_nack",
            kind=SpanKind.CONSUMER,
            attributes={
                "mas.agent_id": agent_id,
                "mas.instance_id": instance_id,
                "mas.retryable": retryable,
            },
        ):
            lease = self._sessions.lease(agent_id, instance_id)
            if await self._sessions.leases.owner(agent_id, instance_id) != lease.owner:
                return
            inflight = await self._sessions.pop_inflight(
                agent_id=agent_id,
                instance_id=instance_id,
                delivery_id=delivery_id,
            )
            if not inflight:
                return

            if retryable and inflight.attempt < self._settings.max_delivery_attempts:
                try:
                    await self._redis.eval(
                        _REQUEUE_AND_ACK_SCRIPT,
                        2,
                        inflight.stream_name,
                        f"mas.session:{agent_id}:{instance_id}",
                        inflight.group,
                        inflight.entry_id,
                        inflight.envelope_json,
                        inflight.attempt + 1,
                        inflight.consumer,
                        lease.owner,
                    )
                except RedisError:
                    telemetry.record_redis_error(
                        component="delivery", operation="xadd_retryable_nack"
                    )
                    logger.warning(
                        "Failed to requeue retryable delivery",
                        exc_info=True,
                        extra={
                            "agent_id": agent_id,
                            "instance_id": instance_id,
                            "delivery_id": delivery_id,
                            "stream_name": inflight.stream_name,
                        },
                    )
                    raise
            else:
                dlq_written = await self._router.write_dlq(
                    envelope_json=inflight.envelope_json,
                    reason=f"{reason}; retry_limit_exceeded" if retryable else reason,
                    delivery=DeliveryCommit(inflight, lease),
                )
                if not dlq_written:
                    return

            telemetry.record_delivery_nack(retryable=retryable)
            if self._circuit_breaker:
                await self._circuit_breaker.record_failure(agent_id, reason=reason)

    async def _ack_inflight(
        self, inflight: InflightDelivery, *, consumer: str, lease: SessionLease
    ) -> bool:
        """Best-effort ACK for a stream entry."""
        try:
            acknowledged = await self._redis.eval(
                _ACK_AND_DELETE_SCRIPT,
                2,
                inflight.stream_name,
                f"mas.session:{lease.agent_id}:{lease.instance_id}",
                inflight.group,
                inflight.entry_id,
                consumer,
                lease.owner,
            )
            return acknowledged == 1
        except RedisError:
            get_telemetry().record_redis_error(component="delivery", operation="xack")
            logger.debug(
                "Failed to ACK inflight delivery",
                exc_info=True,
                extra={
                    "stream_name": inflight.stream_name,
                    "group": inflight.group,
                    "entry_id": inflight.entry_id,
                },
            )
            raise

    async def _stream_loop(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
    ) -> None:
        """Consume messages from Redis streams and deliver over gRPC."""
        shared_stream = f"agent.stream:{agent_id}"
        instance_stream = f"agent.stream:{agent_id}:{instance_id}"
        group = "agents"
        session = self._sessions.session(agent_id, instance_id)
        lease = session.lease
        consumer = f"{agent_id}-{instance_id}-{lease.owner}"

        for stream_name in (shared_stream, instance_stream):
            await self._ensure_group_exists(stream_name=stream_name, group=group)

        claim_start_ids: dict[str, str] = {shared_stream: "0-0", instance_stream: "0-0"}
        stream_names = (shared_stream, instance_stream)
        last_reclaim = 0.0
        reclaim_interval = max(1.0, self._settings.reclaim_idle_ms / 1000.0)
        idle_delay = 0.01

        try:
            while self._running:
                if not session.lease.live:
                    raise RpcError(grpc.StatusCode.UNAVAILABLE, "session_lease_lost")
                # Clear before checking capacity so a concurrent release stays visible.
                session.capacity_changed.clear()
                capacity = self._available_capacity(outbound, inflight)
                if capacity <= 0:
                    try:
                        async with asyncio.timeout(0.5):
                            await session.capacity_changed.wait()
                    except TimeoutError:
                        pass
                    continue

                now = time.monotonic()
                if now - last_reclaim >= reclaim_interval:
                    for stream_name in (shared_stream, instance_stream):
                        claim_start_ids[stream_name] = await self._reclaim_pending(
                            stream_name,
                            group,
                            consumer,
                            claim_start_ids[stream_name],
                            agent_id=agent_id,
                            instance_id=instance_id,
                            outbound=outbound,
                            inflight=inflight,
                        )
                    last_reclaim = now

                capacity = self._available_capacity(outbound, inflight)
                if capacity <= 0:
                    continue
                # Redis COUNT is per stream, so budget all selected streams.
                selected_streams = stream_names[: min(len(stream_names), capacity)]
                owned, batches = _STREAM_READ_ADAPTER.validate_python(
                    await self._redis.eval(
                        _READ_SCRIPT,
                        len(selected_streams) + 1,
                        *selected_streams,
                        f"mas.session:{agent_id}:{instance_id}",
                        lease.owner,
                        group,
                        consumer,
                        min(50, capacity // len(selected_streams)),
                    )
                )
                if not owned:
                    raise RpcError(grpc.StatusCode.UNAVAILABLE, "session_lease_lost")
                stream_names = (stream_names[1], stream_names[0])
                if not batches:
                    await asyncio.sleep(idle_delay)
                    idle_delay = min(0.05, idle_delay * 2)
                    continue
                idle_delay = 0.01
                for stream_name, messages in batches:
                    for entry_id, raw_fields in messages:
                        fields = dict(
                            zip(raw_fields[::2], raw_fields[1::2], strict=True)
                        )
                        if len(inflight) >= self._settings.max_in_flight:
                            logger.warning(
                                "In-flight delivery cap reached while processing "
                                "stream batch",
                                extra={
                                    "agent_id": agent_id,
                                    "instance_id": instance_id,
                                    "max_in_flight": self._settings.max_in_flight,
                                    "stream_name": stream_name,
                                },
                            )
                            break
                        envelope_json = fields.get("envelope", "")
                        if not envelope_json:
                            try:
                                await self._redis.eval(
                                    _ACK_AND_DELETE_SCRIPT,
                                    2,
                                    stream_name,
                                    f"mas.session:{agent_id}:{instance_id}",
                                    group,
                                    entry_id,
                                    consumer,
                                    lease.owner,
                                )
                            except Exception:
                                get_telemetry().record_redis_error(
                                    component="delivery",
                                    operation="xack_malformed_stream_entry",
                                )
                                logger.debug(
                                    "Failed to ACK malformed stream entry",
                                    exc_info=True,
                                    extra={
                                        "agent_id": agent_id,
                                        "instance_id": instance_id,
                                        "stream_name": stream_name,
                                        "entry_id": entry_id,
                                    },
                                )
                            continue

                        await self._deliver_entry(
                            agent_id=agent_id,
                            instance_id=instance_id,
                            outbound=outbound,
                            inflight=inflight,
                            stream_name=stream_name,
                            group=group,
                            entry_id=entry_id,
                            envelope_json=envelope_json,
                            attempt_text=fields.get("attempt", "1"),
                        )
        except asyncio.CancelledError:
            pass

    async def _reclaim_pending(
        self,
        stream_name: str,
        group: str,
        consumer: str,
        start_id: str,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
    ) -> str:
        """Reclaim idle pending messages for delivery."""
        capacity = self._available_capacity(outbound, inflight)
        if capacity <= 0:
            return start_id
        lease = self._sessions.lease(agent_id, instance_id)
        try:
            claimed = await self._redis.eval(
                _CLAIM_SCRIPT,
                2,
                stream_name,
                f"mas.session:{agent_id}:{instance_id}",
                lease.owner,
                group,
                consumer,
                self._settings.reclaim_idle_ms,
                start_id,
                min(self._settings.reclaim_batch_size, capacity),
            )
            if claimed is None:
                raise RpcError(grpc.StatusCode.UNAVAILABLE, "session_lease_lost")
            next_start_id, messages, _deleted_ids = _CLAIM_ADAPTER.validate_python(
                claimed
            )
        except RpcError:
            raise
        except Exception:
            get_telemetry().record_redis_error(
                component="delivery", operation="xautoclaim"
            )
            logger.debug(
                "Failed to reclaim pending entries",
                exc_info=True,
                extra={
                    "stream_name": stream_name,
                    "group": group,
                    "consumer": consumer,
                    "start_id": start_id,
                },
            )
            return start_id

        for entry_id, raw_fields in messages:
            if any(
                delivery.stream_name == stream_name
                and delivery.group == group
                and delivery.entry_id == entry_id
                for delivery in inflight.values()
            ):
                continue
            fields = dict(zip(raw_fields[::2], raw_fields[1::2], strict=True))
            if entry_id is None or fields is None:
                continue
            envelope_json = fields.get("envelope", "")
            if not envelope_json:
                try:
                    await self._redis.eval(
                        _ACK_AND_DELETE_SCRIPT,
                        2,
                        stream_name,
                        f"mas.session:{agent_id}:{instance_id}",
                        group,
                        entry_id,
                        consumer,
                        lease.owner,
                    )
                except Exception:
                    get_telemetry().record_redis_error(
                        component="delivery",
                        operation="xack_reclaimed_malformed_entry",
                    )
                    logger.debug(
                        "Failed to ACK reclaimed malformed entry",
                        exc_info=True,
                        extra={
                            "agent_id": agent_id,
                            "instance_id": instance_id,
                            "stream_name": stream_name,
                            "entry_id": entry_id,
                        },
                    )
                continue

            await self._deliver_entry(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
                stream_name=stream_name,
                group=group,
                entry_id=entry_id,
                envelope_json=envelope_json,
                attempt_text=fields.get("attempt", "1"),
            )

        return next_start_id

    async def _deliver_entry(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
        stream_name: str,
        group: str,
        entry_id: str,
        envelope_json: str,
        attempt_text: str = "1",
    ) -> None:
        """Send a single stream entry to the client."""
        lease = self._sessions.lease(agent_id, instance_id)
        consumer = f"{agent_id}-{instance_id}-{lease.owner}"
        try:
            attempt = int(attempt_text)
        except ValueError:
            attempt = 0
        if not 1 <= attempt <= self._settings.max_delivery_attempts:
            await self._router.write_dlq(
                envelope_json=envelope_json,
                reason="invalid_delivery_attempt",
                delivery=DeliveryCommit(
                    InflightDelivery(
                        stream_name=stream_name,
                        group=group,
                        entry_id=entry_id,
                        envelope_json=envelope_json,
                        received_at=time.time(),
                        consumer=consumer,
                    ),
                    lease,
                ),
            )
            return
        telemetry = get_telemetry()
        try:
            message = EnvelopeMessage.model_validate_json(envelope_json)
        except ValidationError:
            message = None
        parent = (
            telemetry.extract_message_meta_context(message.meta)
            if message is not None
            else None
        )
        with telemetry.start_span(
            "mas.server.delivery.deliver_entry",
            kind=SpanKind.CONSUMER,
            context=parent,
            attributes={
                "mas.agent_id": agent_id,
                "mas.instance_id": instance_id,
                "mas.stream_name": stream_name,
            },
        ) as span:
            delivery_id = uuid.uuid4().hex
            span.set_attribute("mas.delivery_id", delivery_id)
            if message is not None:
                span.set_attribute("mas.message_id", message.message_id)
            timestamp = entry_id.partition("-")[0]
            if timestamp.isascii() and timestamp.isdigit():
                stream_timestamp = int(timestamp)
                if stream_timestamp <= 2**63 - 1:
                    span.set_attribute("mas.redis.entry_timestamp_ms", stream_timestamp)
            event = OutboundDelivery(
                delivery=mas_pb2.Delivery(
                    delivery_id=delivery_id,
                    envelope_json=envelope_json,
                ),
                message_id=message.message_id if message is not None else None,
                parent=parent,
            )

            dropped = self._sessions.drop_oldest_outbound(outbound, inflight)
            if dropped:
                logger.warning(
                    "Outbound queue full; dropped oldest deliveries",
                    extra={
                        "agent_id": agent_id,
                        "instance_id": instance_id,
                        "dropped": dropped,
                    },
                )

            try:
                outbound.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Outbound queue full; dropping new delivery",
                    extra={
                        "agent_id": agent_id,
                        "instance_id": instance_id,
                        "delivery_id": delivery_id,
                    },
                )
                return

            inflight[delivery_id] = InflightDelivery(
                stream_name=stream_name,
                group=group,
                entry_id=entry_id,
                envelope_json=envelope_json,
                received_at=time.time(),
                attempt=attempt,
                consumer=consumer,
            )
            span.set_attribute(
                "mas.delivery.queued_at_unix_ns",
                int(inflight[delivery_id].received_at * 1_000_000_000),
            )

    async def _ensure_group_exists(self, *, stream_name: str, group: str) -> None:
        """Create a stream group, tolerating already-existing groups."""
        try:
            await self._redis.xgroup_create(stream_name, group, id="0-0", mkstream=True)
            return
        except ResponseError as exc:
            if str(exc).startswith("BUSYGROUP"):
                return
            get_telemetry().record_redis_error(
                component="delivery", operation="xgroup_create"
            )
            raise
        except Exception:
            get_telemetry().record_redis_error(
                component="delivery", operation="xgroup_create"
            )
            raise
