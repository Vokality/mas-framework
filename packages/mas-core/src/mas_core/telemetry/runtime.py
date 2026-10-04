"""OpenTelemetry runtime wiring and instrumentation helpers."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Final

from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import (
    Span,
    Status,
    StatusCode,
    Tracer,
)
from opentelemetry.trace import (
    SpanKind as OTelSpanKind,
)

from ..observability import ExportHealth, ObservationSettings, ObservedSpan
from ..protocol import MessageMeta
from .observations import (
    ExportTracker,
    ObservedMetricExporter,
    ObservedSpanExporter,
    SpanJournal,
    current_broker_id,
)

logger = logging.getLogger(__name__)

_TRACE_HEADER_KEYS: Final[tuple[str, ...]] = ("traceparent", "tracestate")
_MAX_BROKER_SCOPES: Final[int] = 256


class SpanKind(Enum):
    """Span kind used by MAS instrumentation."""

    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Normalized telemetry configuration used by runtime bootstrap."""

    enabled: bool = False
    service_name: str = "mas-framework"
    service_namespace: str = "mas"
    environment: str = "dev"
    otlp_endpoint: str | None = None
    sample_ratio: float = 1.0
    export_metrics: bool = True
    metrics_export_interval_ms: int = 60_000
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    """Bounded process counters available even when OTLP export is disabled."""

    ingress: dict[str, int]
    delivery_acks: int
    delivery_nacks: int
    retryable_nacks: int
    dead_letter_writes: int
    dead_letter_errors: int
    redis_errors: int
    active_sessions: int
    policy_samples: int
    policy_latency_mean_ms: float
    policy_latency_max_ms: float
    export_enabled: bool
    scope_complete: bool = True
    dropped_scope_updates: int = 0


@dataclass(slots=True)
class _TelemetryCounters:
    """Shared process counters retained through exporter reconfiguration."""

    lock: Lock = field(default_factory=Lock)
    ingress_counts: dict[str, int] = field(default_factory=dict)
    acks: int = 0
    nacks: int = 0
    retryable_nacks: int = 0
    dlq_writes: int = 0
    dlq_errors: int = 0
    redis_errors: int = 0
    session_count: int = 0
    policy_samples: int = 0
    policy_latency_sum: float = 0.0
    policy_latency_max: float = 0.0
    brokers: dict[str, _TelemetryCounters] = field(default_factory=dict)
    dropped_scope_updates: int = 0


_SPAN_KIND_MAP: Final[dict[SpanKind, OTelSpanKind]] = {
    SpanKind.INTERNAL: OTelSpanKind.INTERNAL,
    SpanKind.SERVER: OTelSpanKind.SERVER,
    SpanKind.CLIENT: OTelSpanKind.CLIENT,
    SpanKind.PRODUCER: OTelSpanKind.PRODUCER,
    SpanKind.CONSUMER: OTelSpanKind.CONSUMER,
}


@dataclass(slots=True)
class _NoopCounter:
    """No-op metric instrument implementation."""

    def add(
        self, amount: int | float, attributes: Mapping[str, str] | None = None
    ) -> None:
        """Accept metric updates without side effects."""


@dataclass(slots=True)
class _NoopHistogram:
    """No-op metric instrument implementation."""

    def record(
        self, amount: int | float, attributes: Mapping[str, str] | None = None
    ) -> None:
        """Accept metric updates without side effects."""


type CounterInstrument = Counter | UpDownCounter | _NoopCounter
type HistogramInstrument = Histogram | _NoopHistogram


class TraceContextFilter(logging.Filter):
    """Attach trace/span identifiers to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Populate trace fields on each record."""
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if not span_context.is_valid:
            record.trace_id = ""
            record.span_id = ""
            return True

        record.trace_id = f"{span_context.trace_id:032x}"
        record.span_id = f"{span_context.span_id:016x}"
        return True


@dataclass(slots=True)
class TelemetrySpan:
    """Wrapper over OpenTelemetry span to avoid ad-hoc API usage."""

    _span: Span | None

    def set_attribute(self, key: str, value: str | bool | int | float) -> None:
        """Set a span attribute when a real span is present."""
        if self._span is not None:
            self._span.set_attribute(key, value)

    def record_exception(self, error: BaseException) -> None:
        """Record an exception and set error status."""
        if self._span is not None:
            self._span.record_exception(error)
            self._span.set_status(Status(StatusCode.ERROR, str(error)))


class TelemetryRuntime:
    """Process-wide telemetry runtime."""

    def __init__(
        self,
        *,
        enabled: bool,
        tracer: Tracer | None,
        tracer_provider: TracerProvider | None,
        meter_provider: MeterProvider | None,
        counters: _TelemetryCounters | None = None,
        export_enabled: bool = False,
        journal: SpanJournal | None = None,
        exporters: tuple[ExportTracker, ExportTracker] | None = None,
    ) -> None:
        """Initialize runtime with concrete OTel providers/instruments."""
        self._enabled = enabled
        self._export_enabled = export_enabled
        self._tracer = tracer
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._shutdown = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._drain_lock = asyncio.Lock()
        self._drain_task: asyncio.Task[list[ObservedSpan]] | None = None
        self._drained_spans: deque[ObservedSpan] = deque()
        self._counters = counters if counters is not None else _TelemetryCounters()
        self._journal = journal if journal is not None else SpanJournal()
        self._exporters = (
            exporters
            if exporters is not None
            else (
                ExportTracker("traces", configured=False),
                ExportTracker("metrics", configured=False),
            )
        )
        if tracer_provider is not None:
            tracer_provider.add_span_processor(self._journal)

        if enabled:
            if meter_provider is None:
                raise ValueError("Enabled telemetry requires a meter provider")
            meter = meter_provider.get_meter("mas.telemetry")
            self._messages_ingress_total: CounterInstrument = meter.create_counter(
                name="mas_messages_ingress_total",
                unit="1",
                description="Total ingress messages by decision",
            )
            self._policy_latency_ms: HistogramInstrument = meter.create_histogram(
                name="mas_policy_latency_ms",
                unit="ms",
                description="Policy pipeline latency",
            )
            self._delivery_ack_total: CounterInstrument = meter.create_counter(
                name="mas_delivery_ack_total",
                unit="1",
                description="Total delivery ACKs",
            )
            self._delivery_nack_total: CounterInstrument = meter.create_counter(
                name="mas_delivery_nack_total",
                unit="1",
                description="Total delivery NACKs",
            )
            self._dlq_write_total: CounterInstrument = meter.create_counter(
                name="mas_dlq_write_total",
                unit="1",
                description="Total DLQ write attempts",
            )
            self._redis_errors_total: CounterInstrument = meter.create_counter(
                name="mas_redis_operation_errors_total",
                unit="1",
                description="Total Redis operation errors",
            )
            self._active_sessions: CounterInstrument = meter.create_up_down_counter(
                name="mas_active_sessions",
                unit="1",
                description="Connected MAS sessions",
            )
        else:
            self._messages_ingress_total = _NoopCounter()
            self._policy_latency_ms = _NoopHistogram()
            self._delivery_ack_total = _NoopCounter()
            self._delivery_nack_total = _NoopCounter()
            self._dlq_write_total = _NoopCounter()
            self._redis_errors_total = _NoopCounter()
            self._active_sessions = _NoopCounter()

        if enabled:
            with self._counters.lock:
                self._active_sessions.add(self._counters.session_count)

    @property
    def enabled(self) -> bool:
        """Return whether telemetry is enabled."""
        return self._enabled

    @property
    def is_shutdown(self) -> bool:
        """Return whether providers were already shut down."""
        return self._shutdown

    @contextmanager
    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, str | bool | int | float] | None = None,
        context: Context | None = None,
    ) -> Iterator[TelemetrySpan]:
        """Start a new span as current context."""
        if not self._enabled or self._tracer is None:
            yield TelemetrySpan(None)
            return

        with self._tracer.start_as_current_span(
            name,
            context=context,
            kind=_SPAN_KIND_MAP[kind],
            attributes=attributes,
        ) as span:
            yield TelemetrySpan(span)

    def inject_message_meta(self, meta: MessageMeta) -> None:
        """Inject current trace context into a message envelope."""
        if not self._enabled:
            return

        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        meta.traceparent = carrier.get("traceparent")
        meta.tracestate = carrier.get("tracestate")

    def extract_message_meta_context(self, meta: MessageMeta) -> Context | None:
        """Extract parent context from message envelope metadata."""
        if not self._enabled:
            return None

        carrier: dict[str, str] = {}
        if meta.traceparent:
            carrier["traceparent"] = meta.traceparent
        if meta.tracestate:
            carrier["tracestate"] = meta.tracestate
        if not carrier:
            return None
        return propagate.extract(carrier)

    def grpc_metadata(self) -> list[tuple[str, str]]:
        """Create outgoing gRPC metadata carrying trace context."""
        if not self._enabled:
            return []

        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        return [(key, value) for key, value in carrier.items() if value]

    def extract_grpc_context(
        self, metadata: Sequence[tuple[str, str | bytes]] | None
    ) -> Context | None:
        """Extract context from inbound gRPC metadata."""
        if not self._enabled:
            return None

        if metadata is None:
            return None

        carrier: dict[str, str] = {}
        for key, raw_value in metadata:
            if key.lower() not in _TRACE_HEADER_KEYS:
                continue
            if isinstance(raw_value, bytes):
                try:
                    value = raw_value.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            else:
                value = raw_value
            carrier[key.lower()] = value

        if not carrier:
            return None

        return propagate.extract(carrier)

    def _counter_targets(self) -> tuple[_TelemetryCounters, ...]:
        """Select process and broker counters while the process lock is held."""
        broker_id = current_broker_id()
        if broker_id is None:
            return (self._counters,)
        scoped = self._counters.brokers.get(broker_id)
        if scoped is None:
            if len(self._counters.brokers) >= _MAX_BROKER_SCOPES:
                self._counters.dropped_scope_updates += 1
                return (self._counters,)
            scoped = _TelemetryCounters()
            self._counters.brokers[broker_id] = scoped
        return self._counters, scoped

    def _metric_attributes(self, **attributes: str) -> dict[str, str]:
        broker_id = current_broker_id()
        if broker_id is not None:
            attributes["mas.broker_id"] = broker_id
        return attributes

    def record_ingress(self, *, decision: str) -> None:
        """Record a bounded ingress decision globally and for this broker."""
        label = (
            decision
            if decision
            in {
                "ALLOWED",
                "ALERT",
                "DLP_REDACTED",
                "AUTHZ_DENIED",
                "RATE_LIMITED",
                "CIRCUIT_OPEN",
                "DLP_BLOCKED",
            }
            else "OTHER"
        )
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.ingress_counts[label] = (
                    counters.ingress_counts.get(label, 0) + 1
                )
        self._messages_ingress_total.add(
            1, attributes=self._metric_attributes(decision=label)
        )

    def record_policy_latency(self, *, latency_ms: float, decision: str) -> None:
        """Record policy pipeline latency globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.policy_samples += 1
                counters.policy_latency_sum += latency_ms
                counters.policy_latency_max = max(
                    counters.policy_latency_max, latency_ms
                )
        self._policy_latency_ms.record(
            latency_ms, attributes=self._metric_attributes(decision=decision)
        )

    def record_delivery_ack(self) -> None:
        """Record delivery ACK globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.acks += 1
        self._delivery_ack_total.add(1, attributes=self._metric_attributes())

    def record_delivery_nack(self, *, retryable: bool) -> None:
        """Record delivery NACK globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.nacks += 1
                counters.retryable_nacks += int(retryable)
        self._delivery_nack_total.add(
            1,
            attributes=self._metric_attributes(
                retryable="true" if retryable else "false"
            ),
        )

    def record_dlq_write(self, *, result: str) -> None:
        """Record DLQ write results globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                if result == "success":
                    counters.dlq_writes += 1
                else:
                    counters.dlq_errors += 1
        self._dlq_write_total.add(1, attributes=self._metric_attributes(result=result))

    def record_redis_error(self, *, component: str, operation: str) -> None:
        """Record Redis errors globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.redis_errors += 1
        self._redis_errors_total.add(
            1,
            attributes=self._metric_attributes(
                component=component, operation=operation
            ),
        )

    def update_active_sessions(self, *, delta: int) -> None:
        """Record active session changes globally and for this broker."""
        with self._counters.lock:
            for counters in self._counter_targets():
                counters.session_count += delta
        self._active_sessions.add(delta, attributes=self._metric_attributes())

    def snapshot(self, broker_id: str | None = None) -> TelemetrySnapshot:
        """Copy process or isolated broker counters, with explicit scope overflow."""
        with self._counters.lock:
            counters = self._counters
            complete = True
            if broker_id is not None:
                scoped = counters.brokers.get(broker_id)
                if scoped is None:
                    scoped = _TelemetryCounters()
                    if len(counters.brokers) >= _MAX_BROKER_SCOPES:
                        complete = False
                counters = scoped
            return TelemetrySnapshot(
                ingress=dict(counters.ingress_counts),
                delivery_acks=counters.acks,
                delivery_nacks=counters.nacks,
                retryable_nacks=counters.retryable_nacks,
                dead_letter_writes=counters.dlq_writes,
                dead_letter_errors=counters.dlq_errors,
                redis_errors=counters.redis_errors,
                active_sessions=counters.session_count,
                policy_samples=counters.policy_samples,
                policy_latency_mean_ms=counters.policy_latency_sum
                / counters.policy_samples
                if counters.policy_samples
                else 0.0,
                policy_latency_max_ms=counters.policy_latency_max,
                export_enabled=self._export_enabled and not self._shutdown,
                scope_complete=complete,
                dropped_scope_updates=self._counters.dropped_scope_updates,
            )

    async def drain_spans(
        self, limit: int = 1000, *, policy: ObservationSettings | None = None
    ) -> list[ObservedSpan]:
        """Convert spans off-loop and retain completed work if a caller cancels."""
        if limit <= 0:
            raise ValueError("span drain limit must be positive")
        async with self._drain_lock:
            if not self._drained_spans:
                if self._drain_task is None:
                    self._drain_task = asyncio.create_task(
                        asyncio.to_thread(
                            self._journal.drain, min(limit, 32_768), policy=policy
                        )
                    )
                try:
                    records = await asyncio.shield(self._drain_task)
                except Exception:
                    self._drain_task = None
                    raise
                self._drained_spans.extend(records)
                self._drain_task = None
            return [
                self._drained_spans.popleft()
                for _ in range(min(limit, len(self._drained_spans)))
            ]

    @property
    def dropped_spans(self) -> int:
        """Return journal overflow and invalid span records explicitly."""
        return self._journal.dropped

    def export_health(self) -> list[ExportHealth]:
        """Report actual trace and metric exporter outcomes independently."""
        return [exporter.snapshot() for exporter in self._exporters]

    def install_log_correlation(self) -> None:
        """Attach trace context fields to log records."""
        if not self._enabled:
            return

        root = logging.getLogger()
        if not any(isinstance(f, TraceContextFilter) for f in root.filters):
            root.addFilter(TraceContextFilter())
        for handler in root.handlers:
            if not any(isinstance(f, TraceContextFilter) for f in handler.filters):
                handler.addFilter(TraceContextFilter())

    def _shutdown_providers(self) -> None:
        errors: list[Exception] = []
        for provider in (self._tracer_provider, self._meter_provider):
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception as error:
                    errors.append(error)
        self._shutdown = True
        if errors:
            raise ExceptionGroup("Telemetry provider shutdown failed", errors)

    async def shutdown(self) -> None:
        """Flush both SDK providers off-loop once, preserving shared cleanup."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(
                asyncio.to_thread(self._shutdown_providers)
            )
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._shutdown_task)
            raise


_runtime_lock = Lock()


@dataclass(slots=True)
class _TelemetryRuntimeRef:
    value: TelemetryRuntime | None = None


_runtime = _TelemetryRuntimeRef()


def get_telemetry() -> TelemetryRuntime:
    """Return process telemetry runtime (disabled by default)."""
    runtime = _runtime.value
    if runtime is not None:
        return runtime
    with _runtime_lock:
        if _runtime.value is None:
            _runtime.value = TelemetryRuntime(
                enabled=False,
                tracer=None,
                tracer_provider=None,
                meter_provider=None,
            )
        return _runtime.value


def _configure_telemetry(settings: TelemetryConfig) -> TelemetryRuntime:
    with _runtime_lock:
        runtime = _runtime.value
        if runtime is not None and runtime.enabled and not runtime.is_shutdown:
            return runtime

        if not settings.enabled:
            if _runtime.value is None or _runtime.value.is_shutdown:
                _runtime.value = TelemetryRuntime(
                    enabled=False,
                    tracer=None,
                    tracer_provider=None,
                    meter_provider=None,
                    counters=runtime._counters if runtime is not None else None,
                    journal=runtime._journal if runtime is not None else None,
                )
            return _runtime.value

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.namespace": settings.service_namespace,
                "deployment.environment": settings.environment,
            }
        )

        sampler = ParentBased(TraceIdRatioBased(settings.sample_ratio))
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        trace_health = ExportTracker("traces", configured=bool(settings.otlp_endpoint))
        metric_health = ExportTracker(
            "metrics",
            configured=bool(settings.otlp_endpoint and settings.export_metrics),
            stale_after_seconds=max(120.0, settings.metrics_export_interval_ms / 500),
        )

        if settings.otlp_endpoint:
            trace_exporter = OTLPSpanExporter(
                endpoint=settings.otlp_endpoint.rstrip("/") + "/v1/traces",
                headers=dict(settings.headers),
            )
            tracer_provider.add_span_processor(
                BatchSpanProcessor(ObservedSpanExporter(trace_exporter, trace_health))
            )

        metric_readers: list[PeriodicExportingMetricReader] = []
        if settings.otlp_endpoint and settings.export_metrics:
            metric_exporter = OTLPMetricExporter(
                endpoint=settings.otlp_endpoint.rstrip("/") + "/v1/metrics",
                headers=dict(settings.headers),
            )
            metric_reader = PeriodicExportingMetricReader(
                exporter=ObservedMetricExporter(metric_exporter, metric_health),
                export_interval_millis=settings.metrics_export_interval_ms,
            )
            metric_readers.append(metric_reader)
        meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)

        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        tracer = tracer_provider.get_tracer("mas.telemetry")

        _runtime.value = TelemetryRuntime(
            enabled=True,
            tracer=tracer,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            counters=runtime._counters if runtime is not None else None,
            export_enabled=bool(settings.otlp_endpoint),
            journal=runtime._journal if runtime is not None else None,
            exporters=(trace_health, metric_health),
        )
        _runtime.value.install_log_correlation()

        logger.info(
            "OpenTelemetry enabled",
            extra={
                "service_name": settings.service_name,
                "service_namespace": settings.service_namespace,
                "environment": settings.environment,
                "export_configured": bool(settings.otlp_endpoint),
                "export_metrics": settings.export_metrics,
            },
        )
        return _runtime.value


async def configure_telemetry(settings: TelemetryConfig) -> TelemetryRuntime:
    """Bootstrap SDK resources/providers off-loop once per process."""
    runtime = get_telemetry()
    if runtime._shutdown_task is not None:
        await runtime.shutdown()
    if not runtime.is_shutdown and (runtime.enabled or not settings.enabled):
        return runtime
    task = asyncio.create_task(asyncio.to_thread(_configure_telemetry, settings))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise
