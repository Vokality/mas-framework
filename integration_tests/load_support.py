"""Measured sustained-load acceptance against real MAS infrastructure."""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import socket
import sys
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import grpc.aio as grpc_aio
from aiohttp import web
from mas_agent import Agent
from mas_agent.config import TlsClientConfig
from mas_core import EnvelopeMessage, SpanKind, get_telemetry
from mas_core.durability import RedisDurability, RedisDurabilitySettings
from mas_core.observability import FleetSnapshot, ObservabilityStore
from mas_core.redis_client import SentinelSettings, create_redis_client
from mas_core.telemetry.runtime import TelemetryConfig, configure_telemetry
from mas_gateway.audit_archive import AuditRetentionSettings
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.config import (
    AuditSettings,
    FeaturesSettings,
    GatewaySettings,
    RateLimitSettings,
    RedisSettings,
    TelemetrySettings,
)
from mas_server.dev import dev_server_settings, generate_dev_tls
from mas_server.observation import decode_otlp_spans
from mas_server.runtime import MASServer
from mas_server.types import AgentDefinition
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.trace import get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from redis.asyncio import Redis

from integration_tests.broker_support import BrokerProcess
from integration_tests.production_support import RedisTopology


class LoadPayload(BaseModel):
    """Unique sequence carried through the actual message envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: int


class RedisBuildInfo(BaseModel):
    """Actual Redis build serving the measurement."""

    redis_version: str


@dataclass(frozen=True, slots=True)
class TraceSpanIdentity:
    """The trace and span identity of a message's injected parent context."""

    trace_id: str
    span_id: str


DeliveryStage = Literal["queue", "write", "receive", "handle"]
_DELIVERY_SPANS: dict[str, DeliveryStage] = {
    "mas.server.delivery.deliver_entry": "queue",
    "mas.server.transport.write": "write",
    "mas.agent.transport.receive": "receive",
    "mas.agent.handle_message": "handle",
}


@dataclass(frozen=True, slots=True)
class SpanTiming:
    """Actual exported clocks for one stage of a delivery attempt."""

    started_unix_ns: int
    finished_unix_ns: int


@dataclass(frozen=True, slots=True)
class DeliveryBacklog:
    """Observed consumer capacity when one outbound delivery starts."""

    instance_id: str
    outbound_queue_size: int
    inflight_count: int


@dataclass(frozen=True, slots=True)
class ConsumerDeliveryReport:
    """Distribution and observed backlog for one actual consumer."""

    deliveries: int
    outbound_queue_max: int
    outbound_queue_p95: float | None
    inflight_max: int
    inflight_p95: float | None


@dataclass(slots=True)
class DeliveryTrace:
    """Join actual server and client stages by their delivery identity."""

    message_id: str = ""
    stream_timestamp_ms: int | None = None
    queued_at_unix_ns: int | None = None
    stages: dict[DeliveryStage, SpanTiming] = field(default_factory=dict)
    backlog: DeliveryBacklog | None = None


@dataclass(frozen=True, slots=True)
class DeliveryTraceCoverage:
    """Accepted message coverage of the complete exported delivery path."""

    accepted_messages: int
    fully_observed_messages: int
    incomplete_messages: int


@dataclass(slots=True)
class OTLPReceiver:
    """Decode real exported protobufs and retain evidence for measured messages."""

    spans: Counter[str] = field(default_factory=Counter)
    span_durations_ms: dict[str, list[float]] = field(default_factory=dict)
    handler_traces: dict[str, str] = field(default_factory=dict)
    ingress_spans: dict[TraceSpanIdentity, str] = field(default_factory=dict)
    service_spans: dict[str, Counter[str]] = field(default_factory=dict)
    deliveries: dict[str, DeliveryTrace] = field(default_factory=dict)
    trace_requests: int = 0
    metrics_requests: int = 0
    metric_points: int = 0
    _runner: web.AppRunner | None = None
    endpoint: str = ""
    observation_store: ObservabilityStore | None = None

    async def start(self) -> None:
        """Own a real HTTP endpoint without a port reservation race."""
        application = web.Application(client_max_size=32 * 1024 * 1024)
        application.router.add_post("/v1/traces", self._traces)
        application.router.add_post("/v1/metrics", self._metrics)
        self._runner = web.AppRunner(application)
        await self._runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.setblocking(False)
        _host, port = TypeAdapter(tuple[str, int]).validate_python(
            listener.getsockname()
        )
        await web.SockSite(self._runner, listener).start()
        self.endpoint = f"http://127.0.0.1:{port}"

    async def _traces(self, request: web.Request) -> web.Response:
        export = ExportTraceServiceRequest()
        payload = await request.read()
        export.ParseFromString(payload)
        if self.observation_store is not None:
            # Brokers already publish their native journals to the shared store.
            # Route external agent spans there while retaining every exported
            # broker span below as independent collector evidence.
            observed_export = ExportTraceServiceRequest()
            observed_scope = observed_export.resource_spans.add().scope_spans.add()
            for resource in export.resource_spans:
                for scope in resource.scope_spans:
                    observed_scope.spans.extend(
                        span
                        for span in scope.spans
                        if span.name.startswith("mas.agent.")
                    )
            if observed_scope.spans:
                await self.observation_store.ingest_spans(
                    await asyncio.to_thread(
                        decode_otlp_spans,
                        observed_export.SerializeToString(),
                        limit=32768,
                    )
                )
        self.trace_requests += 1
        for resource in export.resource_spans:
            service = next(
                (
                    attribute.value.string_value
                    for attribute in resource.resource.attributes
                    if attribute.key == "service.name"
                ),
                "unknown",
            )
            service_spans = self.service_spans.setdefault(service, Counter())
            for scope in resource.scope_spans:
                for span in scope.spans:
                    self.spans[span.name] += 1
                    service_spans[span.name] += 1
                    if span.name == "mas.server.ingress.send" and any(
                        attribute.key == "mas.message_type"
                        and attribute.value.string_value == "load.message"
                        for attribute in span.attributes
                    ):
                        self.ingress_spans[
                            TraceSpanIdentity(span.trace_id.hex(), span.span_id.hex())
                        ] = service
                    self.span_durations_ms.setdefault(span.name, []).append(
                        (span.end_time_unix_nano - span.start_time_unix_nano)
                        / 1_000_000
                    )
                    stage = _DELIVERY_SPANS.get(span.name)
                    if stage is not None:
                        attributes = {
                            attribute.key: attribute.value
                            for attribute in span.attributes
                        }
                        identity = attributes.get("mas.delivery_id")
                        if (
                            identity is not None
                            and identity.WhichOneof("value") == "string_value"
                        ):
                            delivery = self.deliveries.setdefault(
                                identity.string_value, DeliveryTrace()
                            )
                            delivery.stages[stage] = SpanTiming(
                                span.start_time_unix_nano, span.end_time_unix_nano
                            )
                            message_id = attributes.get("mas.message_id")
                            if (
                                message_id is not None
                                and message_id.WhichOneof("value") == "string_value"
                            ):
                                delivery.message_id = message_id.string_value
                            timestamp = attributes.get("mas.redis.entry_timestamp_ms")
                            if (
                                timestamp is not None
                                and timestamp.WhichOneof("value") == "int_value"
                            ):
                                delivery.stream_timestamp_ms = timestamp.int_value
                            queued_at = attributes.get("mas.delivery.queued_at_unix_ns")
                            if (
                                queued_at is not None
                                and queued_at.WhichOneof("value") == "int_value"
                            ):
                                delivery.queued_at_unix_ns = queued_at.int_value
                            instance = attributes.get("mas.instance_id")
                            queued = attributes.get("mas.outbound_queue_size")
                            inflight = attributes.get("mas.inflight_count")
                            if (
                                instance is not None
                                and instance.WhichOneof("value") == "string_value"
                                and queued is not None
                                and queued.WhichOneof("value") == "int_value"
                                and inflight is not None
                                and inflight.WhichOneof("value") == "int_value"
                            ):
                                delivery.backlog = DeliveryBacklog(
                                    instance.string_value,
                                    queued.int_value,
                                    inflight.int_value,
                                )
                    if span.name == "mas.agent.handle_message":
                        for attribute in span.attributes:
                            if attribute.key == "mas.message_id":
                                self.handler_traces[attribute.value.string_value] = (
                                    span.trace_id.hex()
                                )
        return web.Response(body=b"", content_type="application/x-protobuf")

    def delivery_latency(self, message_ids: set[str]) -> dict[str, SpanLatency]:
        """Summarize paired clocks on this co-located test topology.

        transport_write measures framework write completion. write_to_receive
        begins at the same write start, so these intervals overlap. Their
        percentiles are independent observations and must not be added.
        """
        observed: dict[str, list[float]] = {}
        for delivery in self.deliveries.values():
            if delivery.message_id not in message_ids:
                continue
            write = delivery.stages.get("write")
            receive = delivery.stages.get("receive")
            handle = delivery.stages.get("handle")
            stream_time = (
                delivery.stream_timestamp_ms * 1_000_000
                if delivery.stream_timestamp_ms is not None
                else None
            )
            pairs = (
                ("stream_to_outbound", stream_time, delivery.queued_at_unix_ns),
                (
                    "outbound_queue",
                    delivery.queued_at_unix_ns,
                    write.started_unix_ns if write else None,
                ),
                (
                    "transport_write",
                    write.started_unix_ns if write else None,
                    write.finished_unix_ns if write else None,
                ),
                (
                    "write_to_receive",
                    write.started_unix_ns if write else None,
                    receive.started_unix_ns if receive else None,
                ),
                (
                    "receive_to_handler",
                    receive.finished_unix_ns if receive else None,
                    handle.started_unix_ns if handle else None,
                ),
                (
                    "outbound_to_handler",
                    delivery.queued_at_unix_ns,
                    handle.started_unix_ns if handle else None,
                ),
            )
            for name, started, finished in pairs:
                if started is not None and finished is not None:
                    observed.setdefault(name, []).append(
                        (finished - started) / 1_000_000
                    )
        return {
            name: SpanLatency(
                len(values), sum(values) / len(values), _percentile(values)
            )
            for name, values in observed.items()
        }

    def exported_ingress(
        self,
        message_parents: Mapping[str, TraceSpanIdentity],
        *,
        services: set[str],
    ) -> set[str]:
        """Find messages whose exact injected ingress span was exported."""
        return {
            message_id
            for message_id, parent in message_parents.items()
            if self.ingress_spans.get(parent) in services
        }

    def delivery_coverage(self, message_ids: set[str]) -> DeliveryTraceCoverage:
        """Count accepted messages with every required stage and paired clock."""
        complete = {
            delivery.message_id
            for delivery in self.deliveries.values()
            if delivery.message_id in message_ids
            and set(_DELIVERY_SPANS.values()) <= delivery.stages.keys()
            and delivery.stream_timestamp_ms is not None
            and delivery.queued_at_unix_ns is not None
        }
        return DeliveryTraceCoverage(
            accepted_messages=len(message_ids),
            fully_observed_messages=len(complete),
            incomplete_messages=len(message_ids - complete),
        )

    def consumer_backlogs(
        self, message_ids: set[str]
    ) -> dict[str, ConsumerDeliveryReport]:
        """Keep consumer distribution and queue evidence distinct from averages."""
        observed: dict[str, list[DeliveryBacklog]] = {}
        for delivery in self.deliveries.values():
            if delivery.message_id in message_ids and delivery.backlog is not None:
                observed.setdefault(delivery.backlog.instance_id, []).append(
                    delivery.backlog
                )
        return {
            instance: ConsumerDeliveryReport(
                deliveries=len(samples),
                outbound_queue_max=max(
                    sample.outbound_queue_size for sample in samples
                ),
                outbound_queue_p95=_percentile(
                    [float(sample.outbound_queue_size) for sample in samples]
                ),
                inflight_max=max(sample.inflight_count for sample in samples),
                inflight_p95=_percentile(
                    [float(sample.inflight_count) for sample in samples]
                ),
            )
            for instance, samples in observed.items()
        }

    async def _metrics(self, request: web.Request) -> web.Response:
        export = ExportMetricsServiceRequest()
        export.ParseFromString(await request.read())
        self.metrics_requests += 1
        for resource in export.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    self.metric_points += (
                        len(metric.sum.data_points)
                        + len(metric.gauge.data_points)
                        + len(metric.histogram.data_points)
                        + len(metric.exponential_histogram.data_points)
                    )
        return web.Response(body=b"", content_type="application/x-protobuf")

    async def stop(self) -> None:
        """Close only this receiver's listener."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


@dataclass(slots=True)
class LoadMeasurements:
    """Keep unique deliveries and causal clocks separate from acceptance."""

    scheduled: dict[int, float] = field(default_factory=dict)
    received: dict[int, float] = field(default_factory=dict)
    accepted: set[int] = field(default_factory=set)
    message_ids: dict[int, str] = field(default_factory=dict)
    message_parents: dict[str, TraceSpanIdentity] = field(default_factory=dict)
    rpc_ms: list[float] = field(default_factory=list)
    send_started: dict[int, float] = field(default_factory=dict)
    errors: Counter[str] = field(default_factory=Counter)
    duplicates: int = 0
    last_accepted: float = 0
    envelope_bytes: int = 0


@dataclass(slots=True)
class LoopLagProbe:
    """Measure scheduling delays with a bounded recent-sample buffer."""

    samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=100_000))
    count: int = 0
    maximum_ms: float = 0

    async def run(self) -> None:
        """Sample every ten milliseconds until the workload cancels the probe."""
        while True:
            deadline = time.perf_counter() + 0.01
            await asyncio.sleep(0.01)
            delay_ms = max(0, (time.perf_counter() - deadline) * 1000)
            self.count += 1
            self.maximum_ms = max(self.maximum_ms, delay_ms)
            self.samples_ms.append(delay_ms)


class LoadConsumer(Agent):
    """Record handler entry after the full transport and policy path."""

    def __init__(
        self, *, measurements: LoadMeasurements, server_addr: str, tls: TlsClientConfig
    ) -> None:
        super().__init__("load_worker", server_addr=server_addr, tls=tls)
        self.measurements = measurements

    async def on_message(self, message: EnvelopeMessage) -> None:
        payload = LoadPayload.model_validate(message.data)
        sequence = payload.sequence
        if sequence in self.measurements.received:
            self.measurements.duplicates += 1
            return
        self.measurements.received[sequence] = time.perf_counter()
        if self.measurements.envelope_bytes == 0:
            self.measurements.envelope_bytes = len(message.model_dump_json().encode())
        self.measurements.message_ids[sequence] = message.message_id
        parent = message.meta.traceparent
        if parent:
            context = TraceContextTextMapPropagator().extract({"traceparent": parent})
            span_context = get_current_span(context).get_span_context()
            if span_context.is_valid and span_context.trace_flags.sampled:
                self.measurements.message_parents[message.message_id] = (
                    TraceSpanIdentity(
                        f"{span_context.trace_id:032x}",
                        f"{span_context.span_id:016x}",
                    )
                )


@dataclass(frozen=True, slots=True)
class SpanLatency:
    """Measured exported span latency for locating acceptance bottlenecks."""

    count: int
    mean_ms: float
    p95_ms: float | None


class CapacityTarget(BaseModel):
    """Explicit workload scaling policy, independent of CPU model performance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: Literal["absolute", "per_cpu"]
    target_rate: int = Field(gt=0)
    rate_per_cpu: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    usable_cpu_count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_basis(self) -> CapacityTarget:
        if self.basis == "per_cpu":
            if self.rate_per_cpu is None or self.usable_cpu_count is None:
                raise ValueError(
                    "per-CPU capacity requires a rate and usable CPU count"
                )
            scaled = self.rate_per_cpu * self.usable_cpu_count
            if not math.isfinite(scaled) or self.target_rate != math.ceil(scaled):
                raise ValueError("target rate must equal the rounded-up per-CPU rate")
        elif self.rate_per_cpu is not None:
            raise ValueError("absolute capacity cannot specify a per-CPU rate")
        return self

    @classmethod
    def resolve(
        cls,
        *,
        rate: int | None = None,
        rate_per_cpu: float | None = None,
        usable_cpu_count: int | None,
    ) -> CapacityTarget:
        """Select the absolute default or round up a known per-CPU budget."""
        if rate is not None and rate_per_cpu is not None:
            raise ValueError("rate and rate_per_cpu are mutually exclusive")
        if rate_per_cpu is None:
            return cls(
                basis="absolute",
                target_rate=1000 if rate is None else rate,
                usable_cpu_count=usable_cpu_count,
            )
        if usable_cpu_count is None or usable_cpu_count <= 0:
            raise ValueError(
                "per-CPU capacity requires a known positive usable CPU count"
            )
        scaled = rate_per_cpu * usable_cpu_count
        if not math.isfinite(scaled) or scaled <= 0:
            raise ValueError("per-CPU capacity must be finite and positive")
        return cls(
            basis="per_cpu",
            target_rate=math.ceil(scaled),
            rate_per_cpu=rate_per_cpu,
            usable_cpu_count=usable_cpu_count,
        )


@dataclass(frozen=True, slots=True)
class LoadReport:
    """Self-contained measured evidence and explicit acceptance gates."""

    passed: bool
    host_platform: str
    cpu: str
    cpu_cores: int | None
    python_version: str
    redis_version: str
    payload_bytes_max: int
    envelope_bytes_sample: int
    broker_count: int
    broker_mode: Literal["process", "in_process"]
    broker_process_ids: list[int]
    broker_exit_codes: list[int | None]
    cpu_measurement_scope: Literal["load_driver", "load_driver_and_brokers"]
    producer_count: int
    consumer_count: int
    concurrent_sends: int
    started_at_utc: str
    acceptance_finished_at_utc: str
    acceptance_interval_seconds: float
    process_cpu_seconds: float
    process_cpu_percent: float
    event_loop_thread_cpu_seconds: float
    event_loop_thread_cpu_percent: float
    event_loop_lag_interval_ms: float
    event_loop_lag_count: int
    event_loop_lag_samples_retained: int
    event_loop_lag_max_ms: float | None
    event_loop_lag_p95_ms: float | None
    duration_seconds: float
    elapsed_seconds: float
    target_rate: int
    capacity: CapacityTarget
    latency_limit_ms: float
    offered_rate: int
    accepted_rate: float
    planned_messages: int
    accepted_messages: int
    received_messages: int
    lost_accepted_messages: int
    duplicate_deliveries: int
    end_to_end_p95_ms: float | None
    send_rpc_p95_ms: float | None
    errors: dict[str, int]
    rbac_denied_probe: bool
    acl_shortcut_present: bool
    sample_ratio: float
    traced_messages: int
    exported_handler_messages: int
    exported_broker_messages: int
    trace_requests: int
    exported_spans: dict[str, int]
    exported_service_spans: dict[str, dict[str, int]]
    span_latency: dict[str, SpanLatency]
    delivery_latency: dict[str, SpanLatency]
    delivery_trace_coverage: DeliveryTraceCoverage
    delivery_consumers: dict[str, ConsumerDeliveryReport]
    metric_points: int
    required_replica_confirmations: int
    actual_redis_replica_processes: int
    wait_for_aof: bool
    audit_capacity: int
    diagnostics_enabled: bool
    fleet_observation: FleetSnapshot
    retained_history_points: int
    retained_trace_details: int
    gates: dict[str, bool]

    def as_dict(self) -> dict[str, object]:
        """Return clean report data for JSON output."""
        data = asdict(self)
        data["capacity"] = self.capacity.model_dump(mode="json")
        data["fleet_observation"] = self.fleet_observation.model_dump(mode="json")
        return TypeAdapter(dict[str, object]).validate_python(data)


def _percentile(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _profile_operation[**ArgsT, ResultT](
    operation: Callable[ArgsT, Awaitable[ResultT]], name: str
) -> Callable[ArgsT, Awaitable[ResultT]]:
    async def profiled(*args: ArgsT.args, **kwargs: ArgsT.kwargs) -> ResultT:
        with get_telemetry().start_span(name, kind=SpanKind.INTERNAL):
            return await operation(*args, **kwargs)

    return profiled


async def run_sustained_load(
    directory: Path,
    *,
    duration: float = 60,
    target_rate: int = 1000,
    latency_limit_ms: float = 300,
    diagnostics: bool = False,
    broker_mode: Literal["process", "in_process"] = "process",
    capacity: CapacityTarget | None = None,
) -> LoadReport:
    """Exercise actual mTLS, RBAC, tracing, audit and durable replicated routing.

    The offered rate includes explicit two-percent headroom. The gate checks
    measured acceptance throughput against the requested rate, not a relaxed
    throughput threshold. Latency begins at each scheduled admission, so an
    overloaded dispatcher cannot hide queue time by starting its clock late.
    """
    if (
        not math.isfinite(duration)
        or not math.isfinite(latency_limit_ms)
        or duration <= 0
        or target_rate <= 0
        or latency_limit_ms <= 0
    ):
        raise ValueError(
            "load duration, rate and latency limit must be finite and positive"
        )
    if diagnostics and broker_mode != "in_process":
        raise ValueError("monkeypatch diagnostics require in_process broker mode")
    if capacity is None:
        capacity = CapacityTarget.resolve(
            rate=target_rate, usable_cpu_count=os.process_cpu_count()
        )
    elif capacity.target_rate != target_rate:
        raise ValueError("capacity metadata must match the actual target rate")
    offered_rate = target_rate + max(1, math.ceil(target_rate * 0.02))
    planned = math.ceil(duration * offered_rate)
    directory.mkdir(parents=True, exist_ok=True)
    topology = RedisTopology(directory / "redis")
    receiver = OTLPReceiver()
    profiling = ExitStack()
    servers: list[MASServer] = []
    broker_processes: list[BrokerProcess] = []
    broker_addresses: list[str] = []
    broker_pids: list[int] = []
    broker_exit_codes: list[int | None] = []
    broker_services: set[str] = set()
    control_redis: Redis | None = None
    agents: list[Agent] = []
    measurements = LoadMeasurements()
    denied_probe = False
    acl_present = False
    redis_version = "unknown"
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        metadata = await asyncio.create_subprocess_exec(
            "sysctl",
            "-n",
            "machdep.cpu.brand_string",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await metadata.communicate()
        if metadata.returncode == 0:
            cpu = output.decode().strip()
    elif Path("/proc/cpuinfo").is_file():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.partition(":")[2].strip()
                break
    await get_telemetry().shutdown()
    try:
        await receiver.start()
        await topology.start()
        identities = frozenset({"load_sender", "load_worker", "load_denied"})
        tls = await asyncio.to_thread(
            generate_dev_tls, directory / "tls", agent_ids=identities
        )
        gateway = GatewaySettings(
            redis=RedisSettings(
                sentinel=SentinelSettings(
                    service_name="mas-primary",
                    addresses=tuple(
                        ("127.0.0.1", item.port) for item in topology.sentinels
                    ),
                ),
                durability=RedisDurabilitySettings(replica_count=1, wait_for_aof=True),
            ),
            features=FeaturesSettings(rbac=True),
            audit=AuditSettings(
                retention=AuditRetentionSettings(
                    max_messages=200_000,
                    max_security_events=100_000,
                    archive_directory=str(directory / "audit-archive"),
                )
            ),
            rate_limit=RateLimitSettings(per_minute=1_000_000, per_hour=10_000_000),
            telemetry=TelemetrySettings(
                enabled=True,
                sample_ratio=1.0,
                otlp_endpoint=receiver.endpoint,
                export_metrics=True,
                metrics_export_interval_ms=1000,
            ),
        )
        definitions = {aid: AgentDefinition(aid, [], {}) for aid in identities}
        for index in range(2):
            settings = dev_server_settings(
                agents=definitions,
                tls=tls,
                listen_addr="127.0.0.1:0",
            )
            if broker_mode == "process":
                broker_gateway = gateway.model_copy(
                    update={
                        "telemetry": gateway.telemetry.model_copy(
                            update={"service_name": f"mas-load-broker-{index}"}
                        )
                    }
                )
                child = BrokerProcess(directory / f"broker-{index}")
                broker_processes.append(child)
                ready = await child.start(settings, broker_gateway)
                broker_addresses.append(ready.listen_addr)
                broker_pids.append(ready.pid)
                broker_services.add(broker_gateway.telemetry.service_name)
                continue
            server = MASServer(settings=settings, gateway=gateway)
            servers.append(server)
            await server.start()
            broker_addresses.append(server.bound_addr)
            broker_pids.append(os.getpid())
            broker_services.add(gateway.telemetry.service_name)
            if diagnostics:
                profiling.enter_context(
                    patch.object(
                        server.authz,
                        "authorize",
                        _profile_operation(
                            server.authz.authorize, "load.diagnostic.authorize"
                        ),
                    )
                )
                assert server._rate_limit is not None
                profiling.enter_context(
                    patch.object(
                        server._rate_limit,
                        "check_rate_limit",
                        _profile_operation(
                            server._rate_limit.check_rate_limit,
                            "load.diagnostic.rate_limit",
                        ),
                    )
                )
                assert server._circuit_breaker is not None
                for operation in ("check_circuit", "record_success"):
                    callback = (
                        server._circuit_breaker.check_circuit
                        if operation == "check_circuit"
                        else server._circuit_breaker.record_success
                    )
                    profiling.enter_context(
                        patch.object(
                            server._circuit_breaker,
                            operation,
                            _profile_operation(
                                callback, f"load.diagnostic.{operation}"
                            ),
                        )
                    )
        if broker_mode == "process":
            await configure_telemetry(
                TelemetryConfig(
                    enabled=True,
                    service_name="mas-load-driver",
                    service_namespace=gateway.telemetry.service_namespace,
                    environment=gateway.telemetry.environment,
                    otlp_endpoint=receiver.endpoint,
                    sample_ratio=1,
                    export_metrics=True,
                    metrics_export_interval_ms=1000,
                )
            )
        control_redis = create_redis_client(
            url=gateway.redis.url,
            sentinel=gateway.redis.sentinel,
            socket_timeout=gateway.redis.socket_timeout,
        )
        observations = ObservabilityStore(control_redis)
        receiver.observation_store = observations
        async with control_redis.client() as connection:
            redis_version = RedisBuildInfo.model_validate(
                await connection.info("server")
            ).redis_version
            authorization = AuthorizationModule(connection, enable_rbac=True)
            await authorization.create_role(
                "load-producer", permissions=["send:load_worker"]
            )
            await authorization.assign_role("load_sender", "load-producer")
            await RedisDurability(gateway.redis.durability).confirm(connection)
            acl_present = bool(
                (await authorization.get_permissions("load_sender"))["allowed"]
            )

        def client_tls(identity: str) -> TlsClientConfig:
            credentials = tls.client(identity)
            return TlsClientConfig(
                root_ca_path=credentials.root_ca_path,
                client_cert_path=credentials.client_cert_path,
                client_key_path=credentials.client_key_path,
            )

        producers = [
            Agent(
                "load_sender",
                server_addr=address,
                tls=client_tls("load_sender"),
            )
            for address in broker_addresses
        ]
        consumers = [
            LoadConsumer(
                measurements=measurements,
                server_addr=address,
                tls=client_tls("load_worker"),
            )
            for address in broker_addresses
        ]
        denied = Agent(
            "load_denied",
            server_addr=broker_addresses[0],
            tls=client_tls("load_denied"),
        )
        agents = [*producers, *consumers, denied]
        for agent in agents:
            await agent.start()
        try:
            await denied.send("load_worker", "load.probe", {"sequence": -1})
        except grpc_aio.AioRpcError as error:
            denied_probe = error.code().name == "PERMISSION_DENIED"
        concurrent_sends = max(
            32, min(4096, math.ceil(target_rate * latency_limit_ms / 1000))
        )
        semaphore = asyncio.Semaphore(concurrent_sends)

        async def send(sequence: int) -> None:
            entered = time.perf_counter()
            measurements.send_started[sequence] = entered
            try:
                await producers[sequence % len(producers)].send(
                    "load_worker",
                    "load.message",
                    {"sequence": sequence},
                )
                measurements.last_accepted = time.perf_counter()
                measurements.accepted.add(sequence)
                measurements.rpc_ms.append(
                    (measurements.last_accepted - entered) * 1000
                )
            except grpc_aio.AioRpcError as error:
                measurements.errors[error.code().name] += 1
            except Exception as error:
                measurements.errors[type(error).__name__] += 1
            finally:
                semaphore.release()

        loop_lag = LoopLagProbe()
        started_at_utc = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        started_process_cpu = time.process_time()
        started_thread_cpu = time.thread_time()
        lag_task = asyncio.create_task(loop_lag.run())
        try:
            async with asyncio.TaskGroup() as tasks:
                for sequence in range(planned):
                    scheduled = started + sequence / offered_rate
                    measurements.scheduled[sequence] = scheduled
                    delay = scheduled - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await semaphore.acquire()
                    tasks.create_task(send(sequence))
        finally:
            acceptance_interval = time.perf_counter() - started
            process_cpu = time.process_time() - started_process_cpu
            thread_cpu = time.thread_time() - started_thread_cpu
            acceptance_finished_at_utc = datetime.now(UTC).isoformat()
            lag_task.cancel()
            await asyncio.gather(lag_task, return_exceptions=True)
        elapsed = max(duration, measurements.last_accepted - started)
        deadline = time.perf_counter() + 15
        while not measurements.accepted <= measurements.received.keys():
            if time.perf_counter() >= deadline:
                break
            await asyncio.sleep(0.02)
        for agent in reversed(agents):
            await agent.stop()
        agents.clear()
        await get_telemetry().shutdown()
        # Keep real broker supervisors active until external parent spans have
        # reached the shared store and produced retained measurements.
        deadline = time.monotonic() + 10
        while True:
            fleet_observation = await observations.fleet()
            if (
                sum(
                    member.observation.counters.accepted_messages
                    for member in fleet_observation.brokers
                )
                == len(measurements.accepted)
                and len(receiver.handler_traces) >= len(measurements.accepted)
                and fleet_observation.performance.latency_coverage is not None
                and fleet_observation.performance.latency_coverage >= 0.95
                and fleet_observation.performance.end_to_end_p95_ms is not None
            ) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        retained_history = await observations.history(limit=120)
        retained_traces = await observations.traces(limit=30)
        for server in reversed(servers):
            await server.stop()
        servers.clear()
        for child in broker_processes:
            broker_exit_codes.append(await child.stop())
        await get_telemetry().shutdown()
        received = measurements.accepted & measurements.received.keys()
        e2e = [
            (measurements.received[sequence] - measurements.scheduled[sequence]) * 1000
            for sequence in received
        ]
        accepted_ids = {
            measurements.message_ids[sequence]
            for sequence in measurements.accepted
            if sequence in measurements.message_ids
        }
        traced = accepted_ids & measurements.message_parents.keys()
        delivery_latency = receiver.delivery_latency(accepted_ids)
        for name, values in (
            (
                "scheduled_to_rpc_start",
                [
                    (entered - measurements.scheduled[sequence]) * 1000
                    for sequence, entered in measurements.send_started.items()
                ],
            ),
            (
                "rpc_start_to_handler",
                [
                    (
                        measurements.received[sequence]
                        - measurements.send_started[sequence]
                    )
                    * 1000
                    for sequence in received
                ],
            ),
        ):
            if values:
                delivery_latency[name] = SpanLatency(
                    len(values), sum(values) / len(values), _percentile(values)
                )
        exported_ids = {
            message_id
            for message_id in traced
            if receiver.handler_traces.get(message_id)
            == measurements.message_parents[message_id].trace_id
        }
        broker_exported = receiver.exported_ingress(
            {
                message_id: measurements.message_parents[message_id]
                for message_id in traced
            },
            services=broker_services,
        )
        exported_broker_services = set(receiver.ingress_spans.values())
        rate = len(measurements.accepted) / elapsed
        p95 = _percentile(e2e)
        gates = {
            "throughput": rate >= target_rate,
            "latency": p95 is not None and p95 < latency_limit_ms,
            "all_planned_accepted": len(measurements.accepted) == planned,
            "no_rpc_errors": not measurements.errors,
            "no_accepted_loss": len(received) == len(measurements.accepted),
            "rbac_enforced": denied_probe and not acl_present,
            "tracing_propagated": len(traced) == len(measurements.accepted),
            "tracing_exported": len(exported_ids) == len(measurements.accepted)
            and receiver.trace_requests > 0,
            "broker_tracing_exported": len(broker_exported)
            == len(measurements.accepted)
            and broker_services <= exported_broker_services,
            "broker_isolation": broker_mode == "process"
            and len(set(broker_pids)) == 2
            and os.getpid() not in broker_pids,
            "broker_shutdown": broker_exit_codes == [0, 0]
            if broker_mode == "process"
            else True,
            "observability_fleet": len(fleet_observation.brokers) == 2
            and fleet_observation.complete,
            "observability_history": bool(retained_history),
            "observability_traces": bool(retained_traces),
            "observability_delivery_latency": (
                fleet_observation.performance.end_to_end_p95_ms is not None
                and fleet_observation.performance.end_to_end_p95_ms < latency_limit_ms
            ),
            "observability_latency_coverage": (
                fleet_observation.performance.counter_complete
                and fleet_observation.performance.latency_coverage is not None
                and fleet_observation.performance.latency_coverage >= 0.95
            ),
        }
        return LoadReport(
            passed=all(gates.values()),
            host_platform=platform.platform(),
            cpu=cpu,
            cpu_cores=os.cpu_count(),
            python_version=platform.python_version(),
            redis_version=redis_version,
            payload_bytes_max=len(json.dumps({"sequence": planned - 1}).encode()),
            envelope_bytes_sample=measurements.envelope_bytes,
            broker_count=2,
            broker_mode=broker_mode,
            broker_process_ids=broker_pids,
            broker_exit_codes=broker_exit_codes,
            cpu_measurement_scope=(
                "load_driver" if broker_mode == "process" else "load_driver_and_brokers"
            ),
            producer_count=2,
            consumer_count=2,
            concurrent_sends=concurrent_sends,
            started_at_utc=started_at_utc,
            acceptance_finished_at_utc=acceptance_finished_at_utc,
            acceptance_interval_seconds=acceptance_interval,
            process_cpu_seconds=process_cpu,
            process_cpu_percent=100 * process_cpu / acceptance_interval,
            event_loop_thread_cpu_seconds=thread_cpu,
            event_loop_thread_cpu_percent=100 * thread_cpu / acceptance_interval,
            event_loop_lag_interval_ms=10,
            event_loop_lag_count=loop_lag.count,
            event_loop_lag_samples_retained=len(loop_lag.samples_ms),
            event_loop_lag_max_ms=(loop_lag.maximum_ms if loop_lag.count else None),
            event_loop_lag_p95_ms=_percentile(list(loop_lag.samples_ms)),
            duration_seconds=duration,
            elapsed_seconds=elapsed,
            target_rate=target_rate,
            capacity=capacity,
            latency_limit_ms=latency_limit_ms,
            offered_rate=offered_rate,
            accepted_rate=rate,
            planned_messages=planned,
            accepted_messages=len(measurements.accepted),
            received_messages=len(received),
            lost_accepted_messages=len(measurements.accepted) - len(received),
            duplicate_deliveries=measurements.duplicates,
            end_to_end_p95_ms=p95,
            send_rpc_p95_ms=_percentile(measurements.rpc_ms),
            errors=dict(measurements.errors),
            rbac_denied_probe=denied_probe,
            acl_shortcut_present=acl_present,
            sample_ratio=1.0,
            traced_messages=len(traced),
            exported_handler_messages=len(exported_ids),
            exported_broker_messages=len(broker_exported),
            trace_requests=receiver.trace_requests,
            exported_spans=dict(receiver.spans),
            exported_service_spans={
                service: dict(spans)
                for service, spans in receiver.service_spans.items()
            },
            span_latency={
                name: SpanLatency(
                    len(values), sum(values) / len(values), _percentile(values)
                )
                for name, values in receiver.span_durations_ms.items()
            },
            delivery_latency=delivery_latency,
            delivery_trace_coverage=receiver.delivery_coverage(accepted_ids),
            delivery_consumers=receiver.consumer_backlogs(accepted_ids),
            metric_points=receiver.metric_points,
            required_replica_confirmations=gateway.redis.durability.replica_count,
            actual_redis_replica_processes=len(topology.nodes) - 1,
            wait_for_aof=True,
            audit_capacity=200_000,
            diagnostics_enabled=diagnostics,
            fleet_observation=fleet_observation,
            retained_history_points=len(retained_history),
            retained_trace_details=len(retained_traces),
            gates=gates,
        )
    finally:
        await asyncio.gather(
            *(agent.stop() for agent in reversed(agents)), return_exceptions=True
        )
        await asyncio.gather(
            *(server.stop() for server in reversed(servers)), return_exceptions=True
        )
        await asyncio.gather(
            *(child.stop() for child in broker_processes), return_exceptions=True
        )
        if control_redis is not None:
            await control_redis.aclose()
        await get_telemetry().shutdown()
        await receiver.stop()
        await topology.stop()
        profiling.close()
