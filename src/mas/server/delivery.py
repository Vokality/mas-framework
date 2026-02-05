"""Delivery workers and ACK/NACK handling."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from .._proto.v1 import mas_pb2
from ..gateway.circuit_breaker import CircuitBreakerModule
from ..redis_types import AsyncRedisProtocol
from .routing import MessageRouter
from .sessions import SessionManager
from .types import InflightDelivery, MASServerSettings

logger = logging.getLogger(__name__)


class DeliveryService:
    """Run stream delivery loops and handle ACK/NACK outcomes."""

    def __init__(
        self,
        *,
        redis: AsyncRedisProtocol,
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

    def start_stream_task(
        self,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
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
        inflight = await self._sessions.pop_inflight(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
        )
        if not inflight:
            return

        await self._ack_inflight(inflight)
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
        inflight = await self._sessions.pop_inflight(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
        )
        if not inflight:
            return

        if retryable:
            try:
                await self._ack_inflight(inflight)
                await self._redis.xadd(
                    inflight.stream_name, {"envelope": inflight.envelope_json}
                )
            except Exception:
                pass
        else:
            await self._router.write_dlq(
                envelope_json=inflight.envelope_json, reason=reason
            )
            await self._ack_inflight(inflight)

        if self._circuit_breaker:
            await self._circuit_breaker.record_failure(agent_id, reason=reason)

    async def _ack_inflight(self, inflight: InflightDelivery) -> None:
        """Best-effort ACK for a stream entry."""
        try:
            await self._redis.xack(
                inflight.stream_name, inflight.group, inflight.entry_id
            )
        except Exception:
            pass

    async def _stream_loop(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, InflightDelivery],
    ) -> None:
        """Consume messages from Redis streams and deliver over gRPC."""
        shared_stream = f"agent.stream:{agent_id}"
        instance_stream = f"agent.stream:{agent_id}:{instance_id}"
        group = "agents"
        consumer = f"{agent_id}-{instance_id}"

        for stream_name in (shared_stream, instance_stream):
            try:
                await self._redis.xgroup_create(
                    stream_name, group, id="$", mkstream=True
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

        claim_start_ids: dict[str, str] = {shared_stream: "0-0", instance_stream: "0-0"}
        last_reclaim = 0.0
        reclaim_interval = max(1.0, self._settings.reclaim_idle_ms / 1000.0)

        try:
            while self._running:
                if len(inflight) >= self._settings.max_in_flight:
                    await asyncio.sleep(0.05)
                    continue

                now = time.time()
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

                items = await self._redis.xreadgroup(
                    group,
                    consumer,
                    streams={shared_stream: ">", instance_stream: ">"},
                    count=50,
                    block=1000,
                )
                if not items:
                    continue

                for stream_name, messages in items:
                    for entry_id, fields in messages:
                        envelope_json = fields.get("envelope", "")
                        if not envelope_json:
                            try:
                                await self._redis.xack(stream_name, group, entry_id)
                            except Exception:
                                pass
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
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, InflightDelivery],
    ) -> str:
        """Reclaim idle pending messages for delivery."""
        try:
            next_start_id, messages, _deleted_ids = await self._redis.xautoclaim(
                stream_name,
                group,
                consumer,
                self._settings.reclaim_idle_ms,
                start_id,
                count=self._settings.reclaim_batch_size,
            )
        except Exception:
            return start_id

        for entry_id, fields in messages:
            envelope_json = fields.get("envelope", "")
            if not envelope_json:
                try:
                    await self._redis.xack(stream_name, group, entry_id)
                except Exception:
                    pass
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
            )

        return next_start_id

    async def _deliver_entry(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, InflightDelivery],
        stream_name: str,
        group: str,
        entry_id: str,
        envelope_json: str,
    ) -> None:
        """Send a single stream entry to the client."""
        delivery_id = uuid.uuid4().hex
        event = mas_pb2.ServerEvent(
            delivery=mas_pb2.Delivery(
                delivery_id=delivery_id,
                envelope_json=envelope_json,
            )
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
        )
