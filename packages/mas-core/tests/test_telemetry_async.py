"""Telemetry SDK work stays off-loop and survives caller cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from threading import Event, Timer, get_ident

import pytest
from mas_core.observability import ObservationSettings, ObservedSpan
from mas_core.telemetry import runtime as telemetry_module
from mas_core.telemetry.observations import SpanJournal
from mas_core.telemetry.runtime import (
    TelemetryConfig,
    TelemetryRuntime,
    configure_telemetry,
    get_telemetry,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from pytest import MonkeyPatch


@pytest.mark.asyncio
async def test_bootstrap_keeps_getter_responsive_and_finishes_after_cancellation(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        telemetry_module, "_runtime", telemetry_module._TelemetryRuntimeRef()
    )
    initial = get_telemetry()
    entered, release = Event(), Event()
    creations = 0
    worker_thread: int | None = None

    def create_resource(
        attributes: Mapping[str, object] | None = None,
        schema_url: str | None = None,
    ) -> Resource:
        nonlocal creations, worker_thread
        creations += 1
        worker_thread = get_ident()
        entered.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test bootstrap did not release")
        return Resource({"service.name": "mas-server"})

    monkeypatch.setattr(telemetry_module.Resource, "create", create_resource)
    escape = Timer(1, release.set)
    escape.start()
    first = asyncio.create_task(configure_telemetry(TelemetryConfig(enabled=True)))
    second: asyncio.Task[TelemetryRuntime] | None = None
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        assert get_telemetry() is initial
        assert not first.done()
        assert worker_thread != get_ident()
        second = asyncio.create_task(configure_telemetry(TelemetryConfig(enabled=True)))
        first.cancel()
        await asyncio.sleep(0.01)
        assert not first.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        configured = await second
        assert configured is get_telemetry()
        assert configured.enabled
        assert creations == 1
    finally:
        release.set()
        escape.cancel()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )
        await get_telemetry().shutdown()


class _ShutdownExporter(SpanExporter):
    """A real SDK processor delegates shutdown to a controlled blocking exporter."""

    def __init__(self, *, fail: bool = False) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self.fail = fail
        self.thread: int | None = None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.calls += 1
        self.thread = get_ident()
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test shutdown did not release")
        if self.fail:
            raise RuntimeError("exporter close failed")


class _TrackingMeter(MeterProvider):
    """Count actual provider shutdown attempts."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def shutdown(self, timeout_millis: float = 30_000) -> None:
        self.calls += 1
        super().shutdown(timeout_millis)


@pytest.mark.asyncio
async def test_concurrent_shutdown_survives_waiter_cancellation() -> None:
    exporter = _ShutdownExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter = _TrackingMeter()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=meter,
    )
    escape = Timer(1, exporter.release.set)
    escape.start()
    first = asyncio.create_task(runtime.shutdown())
    second = asyncio.create_task(runtime.shutdown())
    try:
        assert await asyncio.to_thread(exporter.entered.wait, 0.5)
        assert exporter.thread != get_ident()
        first.cancel()
        await asyncio.sleep(0.01)
        assert not first.done() and not second.done()
        exporter.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        await runtime.shutdown()
        assert runtime.is_shutdown
        assert exporter.calls == meter.calls == 1
    finally:
        exporter.release.set()
        escape.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_attempts_both_providers_and_retains_failure() -> None:
    exporter = _ShutdownExporter(fail=True)
    exporter.release.set()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter = _TrackingMeter()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=meter,
    )
    with pytest.raises(ExceptionGroup, match="shutdown failed"):
        await runtime.shutdown()
    with pytest.raises(ExceptionGroup, match="shutdown failed"):
        await runtime.shutdown()
    assert exporter.calls == meter.calls == 1
    assert runtime.is_shutdown


@pytest.mark.asyncio
async def test_configure_waits_for_closing_provider_before_replacement(
    monkeypatch: MonkeyPatch,
) -> None:
    exporter = _ShutdownExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    current = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
    )
    monkeypatch.setattr(
        telemetry_module,
        "_runtime",
        telemetry_module._TelemetryRuntimeRef(current),
    )
    escape = Timer(1, exporter.release.set)
    escape.start()
    closing = asyncio.create_task(current.shutdown())
    configuring: asyncio.Task[TelemetryRuntime] | None = None
    try:
        assert await asyncio.to_thread(exporter.entered.wait, 0.5)
        configuring = asyncio.create_task(
            configure_telemetry(TelemetryConfig(enabled=True)),
        )
        await asyncio.sleep(0.01)
        assert not configuring.done()
        assert get_telemetry() is current
        exporter.release.set()
        await closing
        replacement = await configuring
        assert replacement is not current
        assert replacement.enabled and not replacement.is_shutdown
        assert current.is_shutdown
    finally:
        exporter.release.set()
        escape.cancel()
        await asyncio.gather(
            closing,
            *([configuring] if configuring is not None else []),
            return_exceptions=True,
        )
        await get_telemetry().shutdown()


class _BlockingJournal(SpanJournal):
    """Block conversion in a worker while real ended SDK spans remain queued."""

    def __init__(self) -> None:
        super().__init__(capacity=4)
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self.thread: int | None = None

    def drain(
        self, limit: int = 1000, *, policy: ObservationSettings | None = None
    ) -> list[ObservedSpan]:
        self.calls += 1
        self.thread = get_ident()
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test drain did not release")
        return super().drain(limit, policy=policy)


@pytest.mark.asyncio
async def test_cancelled_drain_preserves_records_and_next_call_limit() -> None:
    journal = _BlockingJournal()
    provider = TracerProvider()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
        journal=journal,
    )
    for index in range(4):
        with runtime.start_span(
            "mas.rpc.send", attributes={"mas.message_id": str(index)}
        ):
            pass
    escape = Timer(1, journal.release.set)
    escape.start()
    first = asyncio.create_task(runtime.drain_spans(4))
    try:
        assert await asyncio.to_thread(journal.entered.wait, 0.5)
        assert journal.thread != get_ident()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(runtime.drain_spans(2))
        await asyncio.sleep(0.01)
        assert not second.done()
        journal.release.set()
        recovered = await second + await runtime.drain_spans(2)
        assert [span.attributes["mas.message_id"] for span in recovered] == [
            "0",
            "1",
            "2",
            "3",
        ]
        assert journal.calls == 1
        assert await runtime.drain_spans() == []
        with pytest.raises(ValueError, match="positive"):
            await runtime.drain_spans(0)
    finally:
        journal.release.set()
        escape.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await runtime.shutdown()
