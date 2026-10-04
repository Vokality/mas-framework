"""Broker heartbeat supervision and safe OTLP observation boundaries."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from itertools import islice
from typing import TYPE_CHECKING
from uuid import uuid4

from google.protobuf.message import DecodeError
from mas_core.observability import (
    BrokerCounters,
    BrokerObservation,
    BrokerSession,
    ObservabilityStore,
    ObservationSettings,
    ObservedSpan,
)
from mas_core.telemetry.observations import (
    broker_scope,
    observed_service_name,
    sanitize_attributes,
)
from mas_core.telemetry.runtime import get_telemetry
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

if TYPE_CHECKING:
    from .sessions import SessionManager

logger = logging.getLogger(__name__)


def decode_otlp_spans(payload: bytes, *, limit: int) -> list[ObservedSpan]:
    """Validate a bounded protobuf batch and discard non-operational metadata."""
    export = ExportTraceServiceRequest()
    try:
        export.ParseFromString(payload)
    except DecodeError as exc:
        raise ValueError("invalid_protobuf") from exc
    observed: list[ObservedSpan] = []
    span_count = 0
    for resource in export.resource_spans:
        for scope in resource.scope_spans:
            for span in scope.spans:
                span_count += 1
                if span_count > limit:
                    raise ValueError("too_many_spans")
                if (
                    len(span.trace_id) != 16
                    or not any(span.trace_id)
                    or len(span.span_id) != 8
                    or not any(span.span_id)
                    or (
                        span.parent_span_id
                        and (
                            len(span.parent_span_id) != 8
                            or not any(span.parent_span_id)
                        )
                    )
                    or span.start_time_unix_nano <= 0
                    or span.end_time_unix_nano < span.start_time_unix_nano
                ):
                    raise ValueError("invalid_span")
                service_name = observed_service_name(span.name)
                if service_name is None:
                    continue
                attributes: dict[str, object] = {}
                for attribute in span.attributes:
                    kind = attribute.value.WhichOneof("value")
                    if kind == "string_value":
                        attributes[attribute.key] = attribute.value.string_value
                    elif kind == "bool_value":
                        attributes[attribute.key] = attribute.value.bool_value
                    elif kind == "int_value":
                        attributes[attribute.key] = attribute.value.int_value
                    elif kind == "double_value":
                        attributes[attribute.key] = attribute.value.double_value
                observed.append(
                    ObservedSpan(
                        trace_id=span.trace_id.hex(),
                        span_id=span.span_id.hex(),
                        parent_span_id=span.parent_span_id.hex()
                        if span.parent_span_id
                        else None,
                        name=span.name,
                        service_name=service_name,
                        started_unix_ns=span.start_time_unix_nano,
                        finished_unix_ns=span.end_time_unix_nano,
                        failed=span.status.code == 2,
                        attributes=sanitize_attributes(attributes),
                    )
                )
    return observed


class BrokerObservationSupervisor:
    """Publish leased fleet health and drain bounded spans without browser polling."""

    def __init__(
        self,
        *,
        broker_id: str,
        settings: ObservationSettings,
        redis: Redis,
        sessions: SessionManager,
        is_running: Callable[[], bool],
        listen_addr: str,
    ) -> None:
        """Bind observations to the broker's existing connection and workers."""
        self.store = ObservabilityStore(redis, settings=settings)
        self.management_url: str | None = None
        self._broker_id = broker_id
        self._instance_id = uuid4().hex
        self._settings = settings
        self._redis = redis
        self._sessions = sessions
        self._is_running = is_running
        self._listen_addr = listen_addr
        self._started_at = time.time()
        self._sequence = 0
        self._next_heartbeat = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._pending: deque[ObservedSpan] = deque(maxlen=settings.max_pending_spans)
        self._dropped_pending = 0
        self._baseline = get_telemetry().snapshot(broker_id)

    @property
    def running(self) -> bool:
        """Report whether the background collection worker can still progress."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Publish initial health and own one independent collection worker."""
        if self.running:
            return
        with broker_scope(self._broker_id):
            await self.collect()
            self._task = asyncio.create_task(
                self._run(), name=f"mas-observation-{self._broker_id}"
            )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(min(0.5, self._settings.heartbeat_seconds))
            await self.collect()

    async def collect(self, *, stopped: bool = False) -> None:
        """Drain observations and publish sequenced health within bounded waits."""
        async with self._lock:
            try:
                async with asyncio.timeout(3):
                    telemetry = get_telemetry()
                    draining = asyncio.create_task(
                        telemetry.drain_spans(limit=32_768, policy=self._settings)
                    )
                    try:
                        spans = await asyncio.shield(draining)
                    except asyncio.CancelledError:
                        self._retain_spans(await draining)
                        raise
                    self._retain_spans(spans)
                    while self._pending:
                        batch = list(islice(self._pending, 32_768))
                        await self.store.ingest_spans(batch)
                        for _span in batch:
                            self._pending.popleft()
                        if not stopped:
                            break
                    if stopped or time.monotonic() >= self._next_heartbeat:
                        sessions = await self._sessions.snapshot()
                        workers = [
                            BrokerSession(
                                agent_id=session.agent_id,
                                instance_id=session.instance_id,
                                inflight=len(session.inflight),
                                outbound=session.outbound.qsize(),
                                worker_running=not session.task.done()
                                and session.lease.live,
                            )
                            for session in sessions
                        ]
                        issues = [
                            "Delivery worker stopped"
                            for worker in workers
                            if not worker.worker_running
                        ]
                        redis_available = False
                        latency: float | None = None
                        started = time.monotonic()
                        try:
                            await self._redis.ping()
                            redis_available = True
                            latency = (time.monotonic() - started) * 1000
                        except (RedisError, OSError):
                            issues.append("Redis unavailable")
                        counters = telemetry.snapshot(self._broker_id)
                        if not counters.scope_complete:
                            issues.append("Broker counter coverage incomplete")
                        if telemetry.dropped_spans:
                            issues.append("Process trace journal overflow")
                        if self._dropped_pending:
                            issues.append("Observation backlog overflow")
                        self._sequence += 1
                        await self.store.publish_broker(
                            BrokerObservation(
                                broker_id=self._broker_id,
                                instance_id=self._instance_id,
                                sequence=self._sequence,
                                observed_at=time.time(),
                                started_at=self._started_at,
                                status="stopped"
                                if stopped or not self._is_running()
                                else "degraded"
                                if issues
                                else "healthy",
                                grpc_address=self._listen_addr,
                                management_url=self.management_url,
                                redis_available=redis_available,
                                redis_latency_ms=latency,
                                issues=issues,
                                sessions=workers,
                                counters=BrokerCounters(
                                    accepted_messages=sum(
                                        value
                                        for key, value in counters.ingress.items()
                                        if key in {"ALLOWED", "DLP_REDACTED", "ALERT"}
                                    )
                                    - sum(
                                        value
                                        for key, value in self._baseline.ingress.items()
                                        if key in {"ALLOWED", "DLP_REDACTED", "ALERT"}
                                    ),
                                    rejected_messages=sum(
                                        value
                                        for key, value in counters.ingress.items()
                                        if key
                                        not in {"ALLOWED", "DLP_REDACTED", "ALERT"}
                                    )
                                    - sum(
                                        value
                                        for key, value in self._baseline.ingress.items()
                                        if key
                                        not in {"ALLOWED", "DLP_REDACTED", "ALERT"}
                                    ),
                                    delivery_acks=counters.delivery_acks
                                    - self._baseline.delivery_acks,
                                    delivery_nacks=counters.delivery_nacks
                                    - self._baseline.delivery_nacks,
                                    redis_errors=counters.redis_errors
                                    - self._baseline.redis_errors,
                                    dropped_spans=self._dropped_pending,
                                    scope_complete=counters.scope_complete,
                                    dropped_scope_updates=counters.dropped_scope_updates,
                                ),
                                exporters=telemetry.export_health(),
                            )
                        )
                        self._next_heartbeat = (
                            time.monotonic() + self._settings.heartbeat_seconds
                        )
                    await self.store.flush()
            except (RedisError, OSError, TimeoutError, ValidationError, ValueError):
                logger.exception("Broker observation collection unavailable")

    def _retain_spans(self, spans: list[ObservedSpan]) -> None:
        """Keep consumed journal records even when the awaiting task is cancelled."""
        self._dropped_pending += max(
            0,
            len(self._pending) + len(spans) - self._settings.max_pending_spans,
        )
        self._pending.extend(spans)

    async def stop(self) -> None:
        """Finish the worker and publish stopped health before Redis is closed."""
        async with self._stop_lock:
            if self._task is not None:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
            task = asyncio.create_task(self.collect(stopped=True))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
