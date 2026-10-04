"""Independent fleet heartbeat, crash visibility and lifecycle regressions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from threading import Event, get_ident
from unittest.mock import AsyncMock

import pytest
from mas_core.observability import (
    BrokerObservation,
    ObservabilityStore,
    ObservationSettings,
    ObservedSpan,
)
from mas_core.telemetry.observations import broker_scope
from mas_core.telemetry.runtime import get_telemetry
from mas_gateway.config import GatewaySettings
from mas_server.dev import dev_server_settings
from mas_server.observation import BrokerObservationSupervisor
from mas_server.runtime import MASServer
from mas_server.sessions import SessionManager
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from conftest import TestTlsPaths as TlsPaths


def _server(tls: TlsPaths, identity: str) -> MASServer:
    settings = replace(
        dev_server_settings(agents={}, tls=tls.bundle, listen_addr="127.0.0.1:0"),
        broker_id=identity,
        observations=ObservationSettings(
            heartbeat_seconds=0.05, stale_after_seconds=0.15
        ),
    )
    return MASServer(settings=settings, gateway=GatewaySettings())


async def test_heartbeats_exist_without_http_and_counters_are_broker_scoped(
    redis: Redis, test_tls: TlsPaths
) -> None:
    first, second = _server(test_tls, "first"), _server(test_tls, "second")
    await first.start()
    await second.start()
    try:
        assert first._management is None and second._management is None
        telemetry = get_telemetry()
        with broker_scope("first"):
            telemetry.record_ingress(decision="ALLOWED")
            telemetry.record_ingress(decision="DLP_REDACTED")
        with broker_scope("second"):
            telemetry.record_ingress(decision="ALLOWED")
        store = ObservabilityStore(redis)
        async with asyncio.timeout(2):
            while True:
                fleet = await store.fleet()
                observed = {
                    member.observation.broker_id: member.observation
                    for member in fleet.brokers
                }
                if (
                    observed.get("first")
                    and observed.get("second")
                    and observed["first"].counters.accepted_messages == 2
                    and observed["second"].counters.accepted_messages == 1
                ):
                    break
                await asyncio.sleep(0.02)
        assert observed["first"].instance_id != observed["second"].instance_id
        assert (
            sum(
                observation.counters.accepted_messages
                for observation in observed.values()
            )
            == 3
        )
        assert all(
            observation.counters.scope_complete for observation in observed.values()
        )
    finally:
        await first.stop()
        await second.stop()


async def test_missing_heartbeat_becomes_stale_and_stop_is_retained(
    redis: Redis, test_tls: TlsPaths
) -> None:
    server = _server(test_tls, "crashed")
    await server.start()
    assert server._observations is not None
    supervisor = server._observations
    assert supervisor._task is not None
    supervisor._task.cancel()
    await asyncio.gather(supervisor._task, return_exceptions=True)
    await asyncio.sleep(0.2)
    try:
        store = ObservabilityStore(
            redis,
            settings=ObservationSettings(
                heartbeat_seconds=0.05, stale_after_seconds=0.15
            ),
        )
        member = (await store.fleet()).brokers[0]
        assert member.status == "stale" and not member.fresh
    finally:
        await server.stop()
    member = (await store.fleet()).brokers[0]
    assert member.observation.status == "stopped"
    assert not supervisor.running


async def test_cancelled_stop_publishes_stopped_before_connection_cleanup(
    redis: Redis, test_tls: TlsPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(test_tls, "cancelled")
    await server.start()
    assert server._observations is not None
    supervisor = server._observations
    publish = supervisor.store.publish_broker
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_publish(observation: BrokerObservation) -> None:
        if observation.status == "stopped":
            entered.set()
            await release.wait()
        await publish(observation)

    monkeypatch.setattr(supervisor.store, "publish_broker", delayed_publish)
    stopping = asyncio.create_task(server.stop())
    async with asyncio.timeout(3):
        await entered.wait()
    stopping.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert server._redis is None and server._observations is None
    assert not supervisor.running
    fleet = await ObservabilityStore(redis).fleet()
    assert fleet.brokers[0].observation.status == "stopped"


def test_stable_broker_configuration_rejects_ambiguous_identity(
    test_tls: TlsPaths,
) -> None:
    settings = dev_server_settings(agents={}, tls=test_tls.bundle)
    with pytest.raises(ValueError, match="broker_id"):
        replace(settings, broker_id="../other")
    server = MASServer(
        settings=replace(settings, broker_id="stable"), gateway=GatewaySettings()
    )
    assert server.broker_id == "stable"


async def test_drained_observations_are_retained_until_redis_ingestion_succeeds(
    redis: Redis, test_tls: TlsPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(test_tls, "retry")
    await server.start()
    assert server._observations is not None
    supervisor = server._observations
    assert supervisor._task is not None
    supervisor._task.cancel()
    await asyncio.gather(supervisor._task, return_exceptions=True)
    stamp = time.time_ns()
    supervisor._pending.append(
        ObservedSpan(
            trace_id=f"{100:032x}",
            span_id="1" * 16,
            name="mas.agent.handle_message",
            service_name="mas-agent",
            started_unix_ns=stamp,
            finished_unix_ns=stamp,
        )
    )
    ingest = supervisor.store.ingest_spans
    monkeypatch.setattr(
        supervisor.store,
        "ingest_spans",
        AsyncMock(side_effect=ConnectionError("unavailable")),
    )
    await supervisor.collect()
    assert len(supervisor._pending) == 1
    monkeypatch.setattr(supervisor.store, "ingest_spans", ingest)
    await server.stop()
    assert not supervisor._pending
    assert await ObservabilityStore(redis).trace(f"{100:032x}") is not None


@pytest.mark.parametrize("cancelled", [False, True])
async def test_collection_keeps_asynchronously_drained_records_on_cancellation(
    redis: Redis, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    """Cancelling a collector cannot discard records consumed by its worker."""
    supervisor = BrokerObservationSupervisor(
        broker_id="threaded",
        settings=ObservationSettings(),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        is_running=lambda: True,
        listen_addr="127.0.0.1:1",
    )
    started, release = Event(), Event()
    loop_thread = get_ident()
    conversion_thread: int | None = None
    stamp = time.time_ns()
    record = ObservedSpan(
        trace_id=f"{100:032x}",
        span_id="2" * 16,
        name="mas.agent.handle_message",
        service_name="mas-agent",
        started_unix_ns=stamp,
        finished_unix_ns=stamp,
    )

    def conversion() -> list[ObservedSpan]:
        nonlocal conversion_thread
        conversion_thread = get_ident()
        started.set()
        if not release.wait(2):
            raise TimeoutError("test_conversion_not_released")
        return [record]

    async def drain(
        limit: int = 1000, *, policy: ObservationSettings | None = None
    ) -> list[ObservedSpan]:
        assert limit == 32_768
        assert policy is supervisor._settings
        return await asyncio.to_thread(conversion)

    monkeypatch.setattr(get_telemetry(), "drain_spans", drain)
    collecting = asyncio.create_task(supervisor.collect())
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0.001)
        assert conversion_thread != loop_thread
        if cancelled:
            collecting.cancel()
        release.set()
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await collecting
            assert list(supervisor._pending) == [record]
        else:
            await collecting
            assert not supervisor._pending
    finally:
        release.set()
        await asyncio.gather(collecting, return_exceptions=True)
    if cancelled:

        async def empty_drain(
            limit: int = 1000, *, policy: ObservationSettings | None = None
        ) -> list[ObservedSpan]:
            assert policy is supervisor._settings
            return []

        monkeypatch.setattr(get_telemetry(), "drain_spans", empty_drain)
        await supervisor.collect()
        assert not supervisor._pending
    assert await supervisor.store.trace(record.trace_id) is not None
