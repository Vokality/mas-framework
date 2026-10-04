"""Local operational counters and telemetry provider lifecycle tests."""

from __future__ import annotations

from mas_core.telemetry import runtime as telemetry_module
from mas_core.telemetry.runtime import (
    TelemetryConfig,
    TelemetryRuntime,
    configure_telemetry,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pytest import MonkeyPatch


def test_local_counters_work_without_exporters() -> None:
    runtime = TelemetryRuntime(
        enabled=False, tracer=None, tracer_provider=None, meter_provider=None
    )
    runtime.record_ingress(decision="ALLOWED")
    runtime.record_policy_latency(latency_ms=4.0, decision="ALLOWED")
    runtime.record_policy_latency(latency_ms=2.0, decision="ALLOWED")
    runtime.record_delivery_ack()
    runtime.record_delivery_nack(retryable=True)
    runtime.record_dlq_write(result="failed")
    runtime.record_redis_error(component="delivery", operation="xreadgroup")
    snapshot = runtime.snapshot()
    assert snapshot.ingress == {"ALLOWED": 1}
    assert snapshot.delivery_acks == 1
    assert snapshot.retryable_nacks == 1
    assert snapshot.dead_letter_errors == 1
    assert snapshot.redis_errors == 1
    assert snapshot.policy_latency_mean_ms == 3.0
    assert snapshot.policy_latency_max_ms == 4.0
    snapshot.ingress.clear()
    assert runtime.snapshot().ingress == {"ALLOWED": 1}


async def test_runtime_uses_its_own_meter_provider() -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    tracer = TracerProvider()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=tracer.get_tracer("test"),
        tracer_provider=tracer,
        meter_provider=meter,
    )
    try:
        runtime.record_delivery_ack()
        data = reader.get_metrics_data()
        assert data is not None
        metrics = [
            metric
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        ]
        assert any(metric.name == "mas_delivery_ack_total" for metric in metrics)
    finally:
        await runtime.shutdown()


async def test_reconfiguration_uses_live_tracer(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        telemetry_module, "_runtime", telemetry_module._TelemetryRuntimeRef()
    )
    first = await configure_telemetry(TelemetryConfig(enabled=True))
    await first.shutdown()
    second = await configure_telemetry(TelemetryConfig(enabled=True))
    exporter = InMemorySpanExporter()
    assert second._tracer_provider is not None
    second._tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        with second.start_span("after-restart"):
            pass
        assert [s.name for s in exporter.get_finished_spans()] == ["after-restart"]
    finally:
        await second.shutdown()
    disabled = await configure_telemetry(TelemetryConfig(enabled=False))
    assert not disabled.enabled
    assert not disabled.is_shutdown


async def test_enabling_exporters_preserves_existing_process_counters(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        telemetry_module, "_runtime", telemetry_module._TelemetryRuntimeRef()
    )
    disabled = await configure_telemetry(TelemetryConfig(enabled=False))
    disabled.update_active_sessions(delta=1)
    disabled.record_ingress(decision="ALLOWED")
    enabled = await configure_telemetry(TelemetryConfig(enabled=True))
    try:
        assert enabled.snapshot().active_sessions == 1
        assert enabled.snapshot().ingress == {"ALLOWED": 1}
        enabled.update_active_sessions(delta=-1)
        assert enabled.snapshot().active_sessions == 0
        assert not enabled.snapshot().export_enabled
    finally:
        await enabled.shutdown()
