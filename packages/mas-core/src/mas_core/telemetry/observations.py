"""Bounded safe span observations, broker attribution and exporter outcomes."""

from __future__ import annotations

import math
import re
import time
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Literal

from opentelemetry.context import Context
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
)
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

from ..observability import (
    ExportHealth,
    ObservationAttribute,
    ObservationSettings,
    ObservedSpan,
)

type _ExportError = Literal[
    "timeout", "transport_error", "export_exception", "export_rejected"
]

_BROKER_ID: ContextVar[str | None] = ContextVar("mas.broker_id", default=None)
_SAFE_ATTRIBUTES = frozenset(
    {
        "mas.agent_id",
        "mas.sender_id",
        "mas.target_id",
        "mas.instance_id",
        "mas.message_id",
        "mas.message_type",
        "mas.delivery_id",
        "mas.broker_id",
        "mas.correlation_id",
        "mas.decision",
        "mas.is_reply",
        "mas.retryable",
        "mas.accepted",
        "mas.stream_name",
        "mas.inflight_count",
        "mas.outbound_queue_size",
        "mas.delivery.queued_at_unix_ns",
        "mas.delivery.queue_age_ms",
        "mas.redis.entry_timestamp_ms",
        "rpc.system",
        "rpc.method",
        "rpc.service",
        "rpc.grpc.status_code",
    }
)

_OBSERVED_OPERATIONS = frozenset(
    {
        "mas.agent.discover",
        "mas.agent.handle_message",
        "mas.agent.load_state",
        "mas.agent.reply",
        "mas.agent.request",
        "mas.agent.reset_state",
        "mas.agent.send",
        "mas.agent.start",
        "mas.agent.stop",
        "mas.agent.transport.receive",
        "mas.agent.transport_loop",
        "mas.agent.update_state",
        "mas.gateway.audit.append_batch",
        "mas.gateway.audit.commit_batch",
        "mas.gateway.audit.confirm_batch",
        "mas.gateway.audit.log_message",
        "mas.gateway.audit.query_stream",
        "mas.rpc.discover",
        "mas.rpc.get_state",
        "mas.rpc.reply",
        "mas.rpc.request",
        "mas.rpc.reset_state",
        "mas.rpc.send",
        "mas.rpc.transport",
        "mas.rpc.update_state",
        "mas.server.delivery.deliver_entry",
        "mas.server.delivery.handle_ack",
        "mas.server.delivery.handle_nack",
        "mas.server.ingress.reply",
        "mas.server.ingress.request",
        "mas.server.ingress.send",
        "mas.server.policy.check_admission",
        "mas.server.policy.ingest",
        "mas.server.routing.route_message",
        "mas.server.routing.write_dlq",
        "mas.server.start",
        "mas.server.stop",
        "mas.server.transport.write",
    }
)


def observed_service_name(name: str) -> str | None:
    """Accept static MAS operations and derive a safe service identity."""
    if name not in _OBSERVED_OPERATIONS:
        return None
    if name.startswith("mas.agent."):
        return "mas-agent"
    if name.startswith("mas.gateway."):
        return "mas-gateway"
    return "mas-server"


@contextmanager
def broker_scope(broker_id: str) -> Iterator[None]:
    """Attribute work to a broker without replacing its trace parent context."""
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", broker_id) is None:
        raise ValueError("broker_id requires 1-128 ASCII letters, digits, '-' or '_'")
    token = _BROKER_ID.set(broker_id)
    try:
        yield
    finally:
        _BROKER_ID.reset(token)


def current_broker_id() -> str | None:
    """Return this task's broker attribution, inherited by newly created tasks."""
    return _BROKER_ID.get()


def sanitize_attributes(
    attributes: Mapping[str, object],
) -> dict[str, ObservationAttribute]:
    """Keep only bounded scalar operational metadata; discard arbitrary content."""
    result: dict[str, ObservationAttribute] = {}
    for key in _SAFE_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str):
            result[key] = value[:256]
        elif (isinstance(value, int) and -(2**63) <= value < 2**63) or (
            isinstance(value, float) and math.isfinite(value)
        ):
            result[key] = value
    return result


class SpanJournal(SpanProcessor):
    """Capture ended SDK spans without I/O or waiting for buffer capacity."""

    def __init__(self, capacity: int = 32_768) -> None:
        """Bound retained observations and expose overflow instead of blocking."""
        if capacity <= 0:
            raise ValueError("span journal capacity must be positive")
        self._spans: deque[ReadableSpan] = deque()
        self._capacity = capacity
        self._dropped = 0
        self._lock = Lock()

    @property
    def dropped(self) -> int:
        """Return observations lost to invalid clocks or bounded queue overflow."""
        with self._lock:
            return self._dropped

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        """Attach local broker ownership even when a remote parent is supplied."""
        broker_id = current_broker_id()
        if broker_id is not None:
            span.set_attribute("mas.broker_id", broker_id)

    def on_end(self, span: ReadableSpan) -> None:
        """Enqueue an SDK-finalized span without transforming it on the hot path."""
        if span.name not in _OBSERVED_OPERATIONS:
            return
        with self._lock:
            if len(self._spans) >= self._capacity:
                self._dropped += 1
            else:
                self._spans.append(span)

    def drain(
        self, limit: int = 1000, *, policy: ObservationSettings | None = None
    ) -> list[ObservedSpan]:
        """Convert at most limit finalized spans into safe validated observations."""
        if limit <= 0:
            raise ValueError("span drain limit must be positive")
        with self._lock:
            spans = [self._spans.popleft() for _ in range(min(limit, len(self._spans)))]
        result: list[ObservedSpan] = []
        for span in spans:
            context = span.get_span_context()
            started, finished = span.start_time, span.end_time
            service = observed_service_name(span.name)
            if (
                context is None
                or not context.is_valid
                or started is None
                or finished is None
                or started < 0
                or finished < started
                or service is None
            ):
                with self._lock:
                    self._dropped += 1
                continue
            failed = span.status.status_code is StatusCode.ERROR
            if policy is not None and not policy.retains_span(
                span.name,
                context.trace_id,
                is_reply=(span.attributes or {}).get("mas.is_reply") is True,
                failed=failed,
            ):
                continue
            parent = span.parent
            result.append(
                ObservedSpan(
                    trace_id=f"{context.trace_id:032x}",
                    span_id=f"{context.span_id:016x}",
                    parent_span_id=f"{parent.span_id:016x}"
                    if parent is not None and parent.is_valid
                    else None,
                    name=span.name,
                    service_name=service,
                    started_unix_ns=started,
                    finished_unix_ns=finished,
                    failed=failed,
                    attributes=sanitize_attributes(span.attributes or {}),
                )
            )
        return result

    def shutdown(self) -> None:
        """Keep the final observations available to the owning supervisor."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """No external export is required for the local journal."""
        return True


class ExportTracker:
    """Thread-safe exporter outcomes without endpoints, headers or error text."""

    def __init__(
        self,
        signal: Literal["traces", "metrics"],
        *,
        configured: bool,
        stale_after_seconds: float = 120.0,
    ) -> None:
        """Track one actual exporter signal independently of span observation."""
        self._signal = signal
        self._configured = configured
        self._stale_after_seconds = stale_after_seconds
        self._attempts = self._successes = self._failures = self._items = 0
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self._last_error: _ExportError | None = None
        self._lock = Lock()

    def begin(self) -> None:
        """Mark an actual export invocation, rather than configuration or flush."""
        with self._lock:
            self._attempts += 1
            self._last_attempt = time.time()

    def finish(
        self, *, succeeded: bool, items: int, error: _ExportError | None = None
    ) -> None:
        """Record the SDK's result; only successful batches contribute items."""
        with self._lock:
            if succeeded:
                self._successes += 1
                self._items += items
                self._last_success = time.time()
                self._last_error = None
            else:
                self._failures += 1
                self._last_error = error or "export_rejected"

    def snapshot(self) -> ExportHealth:
        """Return actual attempts and outcomes, even after exporter shutdown."""
        with self._lock:
            age = (
                max(0.0, time.time() - self._last_success)
                if self._last_success is not None
                else None
            )
            status: Literal["disabled", "pending", "healthy", "degraded", "stale"]
            if not self._configured:
                status = "disabled"
            elif self._last_error is not None:
                status = "degraded"
            elif age is None:
                status = "pending"
            elif age > self._stale_after_seconds:
                status = "stale"
            else:
                status = "healthy"
            return ExportHealth(
                signal=self._signal,
                configured=self._configured,
                attempts=self._attempts,
                successes=self._successes,
                failures=self._failures,
                exported_items=self._items,
                last_attempt_at=self._last_attempt,
                last_success_at=self._last_success,
                last_error=self._last_error,
                status=status,
                age_seconds=age,
            )


def _error_category(error: Exception) -> _ExportError:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "transport_error"
    return "export_exception"


class ObservedSpanExporter(SpanExporter):
    """Preserve exporter semantics while observing real span export results."""

    def __init__(self, exporter: SpanExporter, tracker: ExportTracker) -> None:
        """Wrap the concrete exporter; its buffering remains SDK-owned."""
        self._exporter = exporter
        self._tracker = tracker

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Track actual success, rejection or exception without suppressing errors."""
        self._tracker.begin()
        try:
            result = self._exporter.export(spans)
        except Exception as error:
            self._tracker.finish(succeeded=False, items=0, error=_error_category(error))
            raise
        self._tracker.finish(
            succeeded=result is SpanExportResult.SUCCESS, items=len(spans)
        )
        return result

    def shutdown(self) -> None:
        """Close the actual exporter's resources."""
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Preserve the actual exporter's flush contract."""
        return self._exporter.force_flush(timeout_millis=timeout_millis)


class ObservedMetricExporter(MetricExporter):
    """Preserve metric preferences and observe real point-export outcomes."""

    def __init__(self, exporter: MetricExporter, tracker: ExportTracker) -> None:
        """Retain temporality/aggregation preferences used by the SDK reader."""
        super().__init__(
            preferred_temporality=exporter._preferred_temporality,
            preferred_aggregation=exporter._preferred_aggregation,
        )
        self._exporter = exporter
        self._tracker = tracker

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: object,
    ) -> MetricExportResult:
        """Count successful data points; failed or uncertain batches stay failed."""
        self._tracker.begin()
        try:
            result = self._exporter.export(metrics_data, timeout_millis, **kwargs)
        except Exception as error:
            self._tracker.finish(succeeded=False, items=0, error=_error_category(error))
            raise
        items = sum(
            len(metric.data.data_points)
            for resource in metrics_data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        )
        self._tracker.finish(
            succeeded=result is MetricExportResult.SUCCESS, items=items
        )
        return result

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        """Close the underlying exporter with the caller's timeout."""
        self._exporter.shutdown(timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        """Keep the metric exporter's flush timeout and result."""
        return self._exporter.force_flush(timeout_millis)
