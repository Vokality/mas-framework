"""OpenTelemetry runtime wiring and instrumentation helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Final, cast

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
    SpanKind as OTelSpanKind,
    Status,
    StatusCode,
    Tracer,
)

from ..protocol import MessageMeta

logger = logging.getLogger(__name__)

_TRACE_HEADER_KEYS: Final[tuple[str, ...]] = ("traceparent", "tracestate")


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
    ) -> None:
        """Initialize runtime with concrete OTel providers/instruments."""
        self._enabled = enabled
        self._tracer = tracer
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._shutdown = False

        if enabled:
            meter = metrics.get_meter("mas.telemetry")
            self._messages_ingress_total: Counter = meter.create_counter(
                name="mas_messages_ingress_total",
                unit="1",
                description="Total ingress messages by decision",
            )
            self._policy_latency_ms: Histogram = meter.create_histogram(
                name="mas_policy_latency_ms",
                unit="ms",
                description="Policy pipeline latency",
            )
            self._delivery_ack_total: Counter = meter.create_counter(
                name="mas_delivery_ack_total",
                unit="1",
                description="Total delivery ACKs",
            )
            self._delivery_nack_total: Counter = meter.create_counter(
                name="mas_delivery_nack_total",
                unit="1",
                description="Total delivery NACKs",
            )
            self._dlq_write_total: Counter = meter.create_counter(
                name="mas_dlq_write_total",
                unit="1",
                description="Total DLQ write attempts",
            )
            self._redis_errors_total: Counter = meter.create_counter(
                name="mas_redis_operation_errors_total",
                unit="1",
                description="Total Redis operation errors",
            )
            self._active_sessions: UpDownCounter = meter.create_up_down_counter(
                name="mas_active_sessions",
                unit="1",
                description="Connected MAS sessions",
            )
        else:
            self._messages_ingress_total = cast(Counter, _NoopCounter())
            self._policy_latency_ms = cast(Histogram, _NoopHistogram())
            self._delivery_ack_total = cast(Counter, _NoopCounter())
            self._delivery_nack_total = cast(Counter, _NoopCounter())
            self._dlq_write_total = cast(Counter, _NoopCounter())
            self._redis_errors_total = cast(Counter, _NoopCounter())
            self._active_sessions = cast(UpDownCounter, _NoopCounter())

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
        context: object | None = None,
    ) -> Iterator[TelemetrySpan]:
        """Start a new span as current context."""
        if not self._enabled or self._tracer is None:
            yield TelemetrySpan(None)
            return

        with self._tracer.start_as_current_span(
            name,
            context=cast(Context | None, context),
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

    def extract_grpc_context(self, metadata: Sequence[object] | None) -> Context | None:
        """Extract context from inbound gRPC metadata."""
        if not self._enabled:
            return None

        if metadata is None:
            return None

        carrier: dict[str, str] = {}
        for item in metadata:
            key: str | None = None
            value: str | None = None

            if isinstance(item, tuple) and len(item) == 2:
                raw_key, raw_value = item
                if isinstance(raw_key, str):
                    key = raw_key
                if isinstance(raw_value, str):
                    value = raw_value
                elif isinstance(raw_value, bytes):
                    try:
                        value = raw_value.decode("utf-8")
                    except UnicodeDecodeError:
                        value = None
            else:
                raw_key = getattr(item, "key", None)
                raw_value = getattr(item, "value", None)
                if isinstance(raw_key, str):
                    key = raw_key
                if isinstance(raw_value, str):
                    value = raw_value

            if key is None or value is None:
                continue
            if key.lower() in _TRACE_HEADER_KEYS:
                carrier[key.lower()] = value

        if not carrier:
            return None

        return propagate.extract(carrier)

    def record_ingress(self, *, decision: str) -> None:
        """Record ingress decision metric."""
        self._messages_ingress_total.add(1, attributes={"decision": decision})

    def record_policy_latency(self, *, latency_ms: float, decision: str) -> None:
        """Record policy pipeline latency."""
        self._policy_latency_ms.record(latency_ms, attributes={"decision": decision})

    def record_delivery_ack(self) -> None:
        """Record delivery ACK metric."""
        self._delivery_ack_total.add(1)

    def record_delivery_nack(self, *, retryable: bool) -> None:
        """Record delivery NACK metric."""
        self._delivery_nack_total.add(
            1,
            attributes={"retryable": "true" if retryable else "false"},
        )

    def record_dlq_write(self, *, result: str) -> None:
        """Record DLQ write result metric."""
        self._dlq_write_total.add(1, attributes={"result": result})

    def record_redis_error(self, *, component: str, operation: str) -> None:
        """Record Redis operation error metric."""
        self._redis_errors_total.add(
            1,
            attributes={"component": component, "operation": operation},
        )

    def update_active_sessions(self, *, delta: int) -> None:
        """Record active session changes."""
        self._active_sessions.add(delta)

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

    def shutdown(self) -> None:
        """Flush and shutdown telemetry providers."""
        if self._shutdown:
            return
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()
        if self._meter_provider is not None:
            self._meter_provider.shutdown()
        self._shutdown = True


_runtime_lock = Lock()
_runtime: TelemetryRuntime | None = None


def get_telemetry() -> TelemetryRuntime:
    """Return process telemetry runtime (disabled by default)."""
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TelemetryRuntime(
                enabled=False,
                tracer=None,
                tracer_provider=None,
                meter_provider=None,
            )
        return _runtime


def configure_telemetry(settings: TelemetryConfig) -> TelemetryRuntime:
    """Configure global telemetry runtime once per process."""
    global _runtime

    with _runtime_lock:
        if _runtime is not None and _runtime.enabled and not _runtime.is_shutdown:
            return _runtime

        if not settings.enabled:
            if _runtime is None:
                _runtime = TelemetryRuntime(
                    enabled=False,
                    tracer=None,
                    tracer_provider=None,
                    meter_provider=None,
                )
            return _runtime

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.namespace": settings.service_namespace,
                "deployment.environment": settings.environment,
            }
        )

        sampler = ParentBased(TraceIdRatioBased(settings.sample_ratio))
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)

        if settings.otlp_endpoint:
            trace_exporter = OTLPSpanExporter(
                endpoint=settings.otlp_endpoint.rstrip("/") + "/v1/traces",
                headers=dict(settings.headers),
            )
            tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

        meter_provider = MeterProvider(resource=resource)
        if settings.otlp_endpoint and settings.export_metrics:
            metric_exporter = OTLPMetricExporter(
                endpoint=settings.otlp_endpoint.rstrip("/") + "/v1/metrics",
                headers=dict(settings.headers),
            )
            metric_reader = PeriodicExportingMetricReader(
                exporter=metric_exporter,
                export_interval_millis=settings.metrics_export_interval_ms,
            )
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )

        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        tracer = trace.get_tracer("mas.telemetry")

        _runtime = TelemetryRuntime(
            enabled=True,
            tracer=tracer,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
        _runtime.install_log_correlation()

        logger.info(
            "OpenTelemetry enabled",
            extra={
                "service_name": settings.service_name,
                "service_namespace": settings.service_namespace,
                "environment": settings.environment,
                "otlp_endpoint": settings.otlp_endpoint,
                "export_metrics": settings.export_metrics,
            },
        )
        return _runtime
