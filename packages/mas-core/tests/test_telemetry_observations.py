"""Payload-free SDK journaling, actual export outcomes and broker isolation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Barrier, Event, Thread

import pytest
from mas_core.observability import ObservationAttribute, ObservationSettings
from mas_core.telemetry import observations as observation_module
from mas_core.telemetry.observations import (
    ExportTracker,
    ObservedMetricExporter,
    ObservedSpanExporter,
    SpanJournal,
    broker_scope,
    current_broker_id,
    sanitize_attributes,
)
from mas_core.telemetry.runtime import TelemetryRuntime
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import Counter, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
    MetricsData,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)
from pytest import MonkeyPatch


def test_journal_is_bounded_and_excludes_business_content() -> None:
    journal = SpanJournal(capacity=2)
    provider = TracerProvider(resource=Resource({"service.name": "service-secret"}))
    provider.add_span_processor(journal)
    tracer = provider.get_tracer("test")
    try:
        for index in range(3):
            with (
                broker_scope("broker-1"),
                tracer.start_as_current_span(
                    "mas.rpc.send",
                    attributes={
                        "mas.message_id": f"message-{index}",
                        "mas.payload": "business-secret",
                        "http.request.header.authorization": "header-secret",
                    },
                ) as span,
            ):
                span.record_exception(RuntimeError("exception-secret"))
                span.set_status(Status(StatusCode.ERROR, "status-secret"))
        records = journal.drain(1) + journal.drain(10)
        assert len(records) == 2
        assert [record.attributes["mas.message_id"] for record in records] == [
            "message-0",
            "message-1",
        ]
        assert journal.dropped == 1
        assert journal.drain() == []
        assert all(record.failed for record in records)
        assert all(record.service_name == "mas-server" for record in records)
        assert all(
            record.attributes["mas.broker_id"] == "broker-1" for record in records
        )
        assert all(
            record.finished_unix_ns >= record.started_unix_ns for record in records
        )
        assert all("secret" not in record.model_dump_json() for record in records)
    finally:
        provider.shutdown()
    with pytest.raises(ValueError, match="positive"):
        journal.drain(0)


def test_journal_detaches_fifo_batch_before_conversion_and_preserves_new_spans(
    monkeypatch: MonkeyPatch,
) -> None:
    journal = SpanJournal(capacity=2)
    provider = TracerProvider()
    exported = InMemorySpanExporter()
    provider.add_span_processor(journal)
    provider.add_span_processor(SimpleSpanProcessor(exported))
    tracer = provider.get_tracer("test")
    entered, release, produced = Event(), Event(), Event()
    drained: list[str] = []
    original = sanitize_attributes

    def sanitize(attributes: Mapping[str, object]) -> dict[str, ObservationAttribute]:
        if attributes.get("mas.message_id") == "0":
            entered.set()
            if not release.wait(1):
                raise TimeoutError("test conversion was not released")
        return original(attributes)

    def record(index: int) -> None:
        with tracer.start_as_current_span(
            "mas.rpc.send", attributes={"mas.message_id": str(index)}
        ):
            pass

    def drain() -> None:
        drained.extend(
            str(span.attributes["mas.message_id"]) for span in journal.drain(1)
        )

    def produce() -> None:
        record(2)
        record(3)
        produced.set()

    monkeypatch.setattr(observation_module, "sanitize_attributes", sanitize)
    consumer = Thread(target=drain)
    producer = Thread(target=produce)
    try:
        record(0)
        record(1)
        consumer.start()
        assert entered.wait(0.5)
        producer.start()
        assert produced.wait(0.5)
        assert journal.dropped == 1
        release.set()
        consumer.join(timeout=1)
        producer.join(timeout=1)
        assert not consumer.is_alive() and not producer.is_alive()
        assert drained == ["0"]
        assert [span.attributes["mas.message_id"] for span in journal.drain(10)] == [
            "1",
            "2",
        ]
        assert len(exported.get_finished_spans()) == 4
    finally:
        release.set()
        if consumer.ident is not None:
            consumer.join(timeout=1)
        if producer.ident is not None:
            producer.join(timeout=1)
        provider.shutdown()


def test_concurrent_journal_producers_and_drains_preserve_counts_and_order() -> None:
    journal = SpanJournal(capacity=16)
    provider = TracerProvider()
    exported = InMemorySpanExporter()
    provider.add_span_processor(journal)
    provider.add_span_processor(SimpleSpanProcessor(exported))
    tracer = provider.get_tracer("test")
    ready = Barrier(3)
    identities: list[str] = []

    def produce(source: int) -> None:
        ready.wait(timeout=1)
        for index in range(100):
            with tracer.start_as_current_span(
                "mas.rpc.send", attributes={"mas.message_id": f"{source}:{index}"}
            ):
                pass

    producers = [Thread(target=produce, args=(source,)) for source in range(2)]
    try:
        for producer in producers:
            producer.start()
        ready.wait(timeout=1)
        while any(producer.is_alive() for producer in producers):
            identities.extend(
                str(span.attributes["mas.message_id"]) for span in journal.drain(7)
            )
        identities.extend(
            str(span.attributes["mas.message_id"]) for span in journal.drain(1000)
        )
        assert len(identities) == len(set(identities))
        assert len(identities) + journal.dropped == 200
        for source in range(2):
            indices = [
                int(identity.partition(":")[2])
                for identity in identities
                if identity.startswith(f"{source}:")
            ]
            assert indices == sorted(indices)
        assert len(exported.get_finished_spans()) == 200
        assert journal.drain() == []
    finally:
        for producer in producers:
            producer.join(timeout=1)
        provider.shutdown()


def test_journal_defers_conversion_and_preserves_finalized_span(
    monkeypatch: MonkeyPatch,
) -> None:
    """SDK callbacks enqueue only; conversion happens when a consumer drains."""
    journal = SpanJournal(capacity=2)
    provider = TracerProvider()
    provider.add_span_processor(journal)
    conversions = 0

    def sanitize(attributes: Mapping[str, object]) -> dict[str, ObservationAttribute]:
        nonlocal conversions
        conversions += 1
        return sanitize_attributes(attributes)

    monkeypatch.setattr(observation_module, "sanitize_attributes", sanitize)
    try:
        span = provider.get_tracer("test").start_span(
            "mas.rpc.send",
            attributes={"mas.message_id": "original"},
        )
        span.end()
        assert conversions == 0
        span.set_attribute("mas.message_id", "after-end")
        span.update_name("mas.business-secret")
        (record,) = journal.drain()
        assert conversions == 1
        assert record.name == "mas.rpc.send"
        assert record.attributes["mas.message_id"] == "original"
        assert journal.drain() == []
    finally:
        provider.shutdown()


def test_drain_processing_is_bounded_when_finalized_clocks_are_invalid() -> None:
    journal = SpanJournal(capacity=4)
    provider = TracerProvider()
    provider.add_span_processor(journal)
    try:
        tracer = provider.get_tracer("test")
        for _ in range(3):
            span = tracer.start_span("mas.rpc.send", start_time=10)
            span.end(end_time=9)
        span = tracer.start_span("mas.rpc.send", start_time=10)
        span.end(end_time=11)
        assert journal.drain(2) == []
        assert journal.dropped == 2
        (record,) = journal.drain(2)
        assert record.finished_unix_ns == 11
        assert journal.dropped == 3
        assert journal.drain() == []
    finally:
        provider.shutdown()


def test_retention_skips_conversion_but_keeps_required_and_sampled_spans(
    monkeypatch: MonkeyPatch,
) -> None:
    journal = SpanJournal(capacity=100)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(journal)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    policy = ObservationSettings(trace_sample_every=100)
    conversions = 0

    def sanitize(attributes: Mapping[str, object]) -> dict[str, ObservationAttribute]:
        nonlocal conversions
        conversions += 1
        return sanitize_attributes(attributes)

    monkeypatch.setattr(observation_module, "sanitize_attributes", sanitize)
    tracer = provider.get_tracer("test")
    required = {
        "mas.agent.send",
        "mas.agent.request",
        "mas.agent.reply",
        "mas.rpc.send",
        "mas.rpc.request",
        "mas.rpc.reply",
        "mas.server.ingress.send",
        "mas.server.ingress.request",
        "mas.server.ingress.reply",
        "mas.agent.handle_message",
        "mas.agent.transport.receive",
    }
    optional = {
        "mas.server.policy.ingest",
        "mas.server.transport.write",
        "mas.agent.transport.receive",
        "mas.gateway.audit.log_message",
    }

    def record(name: str, trace_id: int, *, is_reply: bool, failed: bool) -> None:
        parent = SpanContext(
            trace_id=trace_id,
            span_id=900,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        with tracer.start_as_current_span(
            name,
            context=set_span_in_context(NonRecordingSpan(parent)),
            attributes={"mas.is_reply": is_reply},
        ) as span:
            if failed:
                span.set_status(Status(StatusCode.ERROR))

    try:
        for name in required:
            record(name, 1, is_reply=True, failed=False)
        for name in optional:
            record(name, 1, is_reply=False, failed=False)
            record(name, 100, is_reply=False, failed=False)
        record("mas.server.policy.ingest", 1, is_reply=False, failed=True)
        assert conversions == 0
        records = journal.drain(100, policy=policy)
        assert conversions == len(records) == len(required) + len(optional) + 1
        assert {
            span.name
            for span in records
            if int(span.trace_id, 16) == 1 and not span.failed
        } == required
        assert {
            span.name for span in records if int(span.trace_id, 16) == 100
        } == optional
        assert any(span.failed and int(span.trace_id, 16) == 1 for span in records)
        assert (
            len(exporter.get_finished_spans()) == len(required) + 2 * len(optional) + 1
        )
        assert journal.dropped == 0
        record("mas.server.policy.ingest", 1, is_reply=False, failed=False)
        assert len(journal.drain()) == 1
    finally:
        provider.shutdown()


@pytest.mark.asyncio
async def test_async_retention_consumes_bounded_raw_spans_before_conversion(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = TracerProvider()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
    )
    parent = SpanContext(
        trace_id=1,
        span_id=900,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    context = set_span_in_context(NonRecordingSpan(parent))
    conversions = 0

    def sanitize(attributes: Mapping[str, object]) -> dict[str, ObservationAttribute]:
        nonlocal conversions
        conversions += 1
        return sanitize_attributes(attributes)

    monkeypatch.setattr(observation_module, "sanitize_attributes", sanitize)
    policy = ObservationSettings(trace_sample_every=100)
    try:
        with runtime.start_span("mas.server.policy.ingest", context=context):
            pass
        with runtime.start_span("mas.agent.handle_message", context=context):
            pass
        assert await runtime.drain_spans(1, policy=policy) == []
        assert conversions == 0
        (record,) = await runtime.drain_spans(1, policy=policy)
        assert record.name == "mas.agent.handle_message" and conversions == 1
        assert runtime.dropped_spans == 0
    finally:
        await runtime.shutdown()


def test_scope_preserves_explicit_remote_parent() -> None:
    journal = SpanJournal()
    provider = TracerProvider()
    provider.add_span_processor(journal)
    remote = SpanContext(
        trace_id=123,
        span_id=456,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    try:
        with (
            broker_scope("broker-2"),
            provider.get_tracer("test").start_as_current_span(
                "mas.rpc.request",
                context=set_span_in_context(NonRecordingSpan(remote)),
            ),
        ):
            pass
        (record,) = journal.drain()
        assert record.trace_id == f"{remote.trace_id:032x}"
        assert record.parent_span_id == f"{remote.span_id:016x}"
        assert record.attributes["mas.broker_id"] == "broker-2"
        assert current_broker_id() is None
    finally:
        provider.shutdown()


def test_arbitrary_operation_and_service_names_stay_out_of_observations() -> None:
    journal = SpanJournal()
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name": "service-secret"}))
    provider.add_span_processor(journal)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("mas.business-secret"):
            pass
        with tracer.start_as_current_span("mas.agent.request"):
            pass
        (record,) = journal.drain()
        assert record.service_name == "mas-agent"
        assert record.name == "mas.agent.request"
        assert "secret" not in record.model_dump_json()
        assert journal.dropped == 0
        assert [span.name for span in exporter.get_finished_spans()] == [
            "mas.business-secret",
            "mas.agent.request",
        ]
    finally:
        provider.shutdown()


def test_attribute_filter_validates_scalar_boundary() -> None:
    assert sanitize_attributes(
        {
            "mas.is_reply": True,
            "mas.message_id": "x" * 300,
            "mas.delivery.queue_age_ms": float("nan"),
            "mas.inflight_count": 2**64,
            "mas.target_id": ["target"],
            "exception.message": "secret",
        }
    ) == {"mas.is_reply": True, "mas.message_id": "x" * 256}


@pytest.mark.asyncio
async def test_scoped_counters_are_isolated_with_telemetry_disabled() -> None:
    runtime = TelemetryRuntime(
        enabled=False,
        tracer=None,
        tracer_provider=None,
        meter_provider=None,
    )

    async def worker(broker_id: str, count: int) -> None:
        with broker_scope(broker_id):
            for _ in range(count):
                runtime.record_ingress(decision="ALLOWED")
                runtime.record_delivery_ack()
                await asyncio.sleep(0)
            runtime.update_active_sessions(delta=1)

    await asyncio.gather(worker("first", 2), worker("second", 3))
    assert runtime.snapshot("first").ingress == {"ALLOWED": 2}
    assert runtime.snapshot("second").delivery_acks == 3
    assert runtime.snapshot().ingress == {"ALLOWED": 5}
    assert runtime.snapshot().active_sessions == 2
    assert runtime.snapshot("first").active_sessions == 1
    assert current_broker_id() is None


def test_broker_registry_overflow_is_explicit_and_keeps_global_counters() -> None:
    runtime = TelemetryRuntime(
        enabled=False,
        tracer=None,
        tracer_provider=None,
        meter_provider=None,
    )
    for index in range(257):
        with broker_scope(f"broker-{index}"):
            runtime.record_delivery_ack()
    assert len(runtime._counters.brokers) == 256
    assert runtime.snapshot().delivery_acks == 257
    assert runtime.snapshot("broker-0").scope_complete
    overflow = runtime.snapshot("broker-256")
    assert not overflow.scope_complete
    assert overflow.dropped_scope_updates == 1


class _SpanExporter(SpanExporter):
    """Controlled exporter outcomes with the SDK's actual public interface."""

    result = SpanExportResult.SUCCESS
    error: Exception | None = None
    closed = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if self.error is not None:
            raise self.error
        return self.result

    def shutdown(self) -> None:
        self.closed = True

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return timeout_millis == 17


def test_span_export_health_observes_results_and_safe_exceptions(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation_module.time, "time", lambda: 100.0)
    tracker = ExportTracker("traces", configured=True)
    delegate = _SpanExporter()
    exporter = ObservedSpanExporter(delegate, tracker)
    assert tracker.snapshot().status == "pending"
    assert exporter.export([]) is SpanExportResult.SUCCESS
    assert tracker.snapshot().status == "healthy"
    delegate.result = SpanExportResult.FAILURE
    assert exporter.export([]) is SpanExportResult.FAILURE
    delegate.error = TimeoutError("https://user:secret@collector/header-secret")
    with pytest.raises(TimeoutError):
        exporter.export([])
    failed = tracker.snapshot()
    assert (failed.attempts, failed.successes, failed.failures) == (3, 1, 2)
    assert failed.last_error == "timeout"
    assert failed.status == "degraded"
    assert "secret" not in failed.model_dump_json()
    delegate.error = None
    delegate.result = SpanExportResult.SUCCESS
    exporter.export([])
    monkeypatch.setattr(observation_module.time, "time", lambda: 221.0)
    assert tracker.snapshot().status == "stale"
    assert tracker.snapshot().age_seconds == 121.0
    assert exporter.force_flush(17)
    exporter.shutdown()
    assert delegate.closed
    assert ExportTracker("traces", configured=False).snapshot().status == "disabled"


class _MetricExporter(MetricExporter):
    """Keep SDK metric preferences and inspect forwarded deadlines."""

    def __init__(self) -> None:
        super().__init__(preferred_temporality={Counter: AggregationTemporality.DELTA})
        self.timeout: float | None = None
        self.result = MetricExportResult.SUCCESS
        self.closed = False

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: object,
    ) -> MetricExportResult:
        self.timeout = timeout_millis
        return self.result

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        self.timeout = timeout_millis
        self.closed = True

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        self.timeout = timeout_millis
        return True


def test_metric_export_health_counts_actual_points_and_preserves_preferences() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    provider.get_meter("test").create_counter("requests").add(2)
    data = reader.get_metrics_data()
    assert data is not None
    delegate = _MetricExporter()
    tracker = ExportTracker("metrics", configured=True)
    exporter = ObservedMetricExporter(delegate, tracker)
    try:
        assert exporter._preferred_temporality == delegate._preferred_temporality
        assert exporter.export(data, timeout_millis=37) is MetricExportResult.SUCCESS
        assert delegate.timeout == 37
        assert tracker.snapshot().exported_items == 1
        delegate.result = MetricExportResult.FAILURE
        exporter.export(data)
        assert tracker.snapshot().exported_items == 1
        assert tracker.snapshot().failures == 1
        assert exporter.force_flush(19)
        assert delegate.timeout == 19
        exporter.shutdown(23)
        assert delegate.closed and delegate.timeout == 23
    finally:
        provider.shutdown()


async def test_runtime_journals_without_an_otlp_endpoint() -> None:
    provider = TracerProvider()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
    )
    try:
        with broker_scope("local"), runtime.start_span("mas.server.start"):
            pass
        (record,) = await runtime.drain_spans()
        assert record.attributes["mas.broker_id"] == "local"
        assert runtime.dropped_spans == 0
        assert all(health.status == "disabled" for health in runtime.export_health())
    finally:
        await runtime.shutdown()


def test_actual_otlp_http_exporter_success_and_rejection_are_observed() -> None:
    """Exercise SDK exporters against real HTTP outcomes, without Redis."""
    responses = iter((200, 400, 200, 400))

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(next(responses))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """Do not include collector request data in test logs."""

    server = HTTPServer(("127.0.0.1", 0), Collector)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    trace_tracker = ExportTracker("traces", configured=True)
    metric_tracker = ExportTracker("metrics", configured=True)
    trace_exporter = ObservedSpanExporter(
        OTLPSpanExporter(
            endpoint=endpoint + "/v1/traces", headers={"secret": "secret"}
        ),
        trace_tracker,
    )
    metric_exporter = ObservedMetricExporter(
        OTLPMetricExporter(endpoint=endpoint + "/v1/metrics"),
        metric_tracker,
    )
    spans = InMemorySpanExporter()
    tracer = TracerProvider()
    tracer.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with tracer.get_tracer("test").start_as_current_span("mas.test"):
            pass
        records = spans.get_finished_spans()
        assert trace_exporter.export(records) is SpanExportResult.SUCCESS
        assert trace_exporter.export(records) is SpanExportResult.FAILURE
        meter.get_meter("test").create_counter("requests").add(1)
        data = reader.get_metrics_data()
        assert data is not None
        assert metric_exporter.export(data) is MetricExportResult.SUCCESS
        assert metric_exporter.export(data) is MetricExportResult.FAILURE
        for health in (trace_tracker.snapshot(), metric_tracker.snapshot()):
            assert (health.attempts, health.successes, health.failures) == (2, 1, 1)
            assert health.exported_items == 1
            assert health.status == "degraded"
            assert "secret" not in health.model_dump_json()
    finally:
        trace_exporter.shutdown()
        metric_exporter.shutdown()
        tracer.shutdown()
        meter.shutdown()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
