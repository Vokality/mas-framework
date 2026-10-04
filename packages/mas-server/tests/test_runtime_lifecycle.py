"""Broker resource ownership and configuration regressions."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from mas_core.telemetry.runtime import get_telemetry
from mas_gateway.config import GatewaySettings
from mas_server.dev import dev_server_settings
from mas_server.runtime import MASServer
from mas_server.types import AgentDefinition
from redis.exceptions import ConnectionError

from conftest import TestTlsPaths as TlsPaths


@pytest.mark.parametrize(
    "field",
    ["max_in_flight", "max_delivery_attempts", "reclaim_batch_size", "reclaim_idle_ms"],
)
def test_delivery_settings_reject_nonpositive_bounds(
    test_tls: TlsPaths, field: str
) -> None:
    settings = dev_server_settings(agents={}, tls=test_tls.bundle)
    with pytest.raises(ValueError, match="positive"):
        replace(settings, **{field: 0})


def test_allowlist_keys_match_identity(test_tls: TlsPaths) -> None:
    with pytest.raises(ValueError, match="match their agent_id"):
        dev_server_settings(
            agents={"different": AgentDefinition("worker", [], {})}, tls=test_tls.bundle
        )


async def test_double_start_owns_one_listener(test_tls: TlsPaths) -> None:
    server = MASServer(
        settings=dev_server_settings(
            agents={}, tls=test_tls.bundle, listen_addr="127.0.0.1:0"
        ),
        gateway=GatewaySettings(),
    )
    await asyncio.gather(server.start(), server.start())
    grpc_server, redis = server._grpc_server, server._redis
    await server.start()
    assert server._grpc_server is grpc_server
    assert server._redis is redis
    await server.stop()


async def test_failed_start_releases_owned_resources(test_tls: TlsPaths) -> None:
    settings = dev_server_settings(
        agents={}, tls=test_tls.bundle, listen_addr="127.0.0.1:0"
    )
    settings = replace(
        settings, tls=replace(settings.tls, server_cert_path="/missing/mas-server.pem")
    )
    server = MASServer(settings=settings, gateway=GatewaySettings())
    with pytest.raises(FileNotFoundError):
        await server.start()
    assert server._redis is None
    assert server._grpc_server is None
    assert server._sessions is None
    assert not server._running
    await server.stop()


async def test_lease_acquisition_failure_rolls_back_session(
    test_tls: TlsPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MASServer(
        settings=dev_server_settings(
            agents={"worker": AgentDefinition("worker", [], {})},
            tls=test_tls.bundle,
            listen_addr="127.0.0.1:0",
        ),
        gateway=GatewaySettings(),
    )
    await server.start()
    try:
        assert server._registry is not None and server._sessions is not None
        monkeypatch.setattr(
            server._sessions.leases,
            "acquire",
            AsyncMock(side_effect=ConnectionError("failure")),
        )
        before = get_telemetry().snapshot().active_sessions
        with pytest.raises(ConnectionError):
            await server.connect_session(agent_id="worker", instance_id="one")
        assert await server._sessions.snapshot() == []
        assert get_telemetry().snapshot().active_sessions == before
    finally:
        await server.stop()


async def test_stopping_one_server_keeps_process_telemetry(
    test_tls: TlsPaths,
) -> None:
    server = MASServer(
        settings=dev_server_settings(
            agents={}, tls=test_tls.bundle, listen_addr="127.0.0.1:0"
        ),
        gateway=GatewaySettings(),
    )
    await server.start()
    runtime = get_telemetry()
    await server.stop()
    assert not runtime.is_shutdown


async def test_cancelled_stop_finishes_all_owned_cleanup(
    test_tls: TlsPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_settings = dev_server_settings(
        agents={}, tls=test_tls.bundle, listen_addr="127.0.0.1:0"
    )
    server = MASServer(settings=from_settings, gateway=GatewaySettings())
    await server.start()
    assert server._redis is not None
    redis = server._redis
    close = redis.aclose
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_close() -> None:
        entered.set()
        await release.wait()
        await close()

    monkeypatch.setattr(redis, "aclose", delayed_close)
    task = asyncio.create_task(server.stop())
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server._redis is None
    assert server._grpc_server is None
    assert server._sessions is None
    await server.stop()
