"""Management HTTP contract and operational health regressions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from mas_core.protocol import EnvelopeMessage
from mas_gateway.audit import AuditModule
from mas_gateway.circuit_breaker import CircuitBreakerConfig, CircuitBreakerModule
from mas_gateway.config import GatewaySettings
from mas_server.dev import dev_server_settings
from mas_server.management import (
    ManagementService,
    ManagementSettings,
    ManagementSnapshot,
)
from mas_server.runtime import MASServer
from mas_server.sessions import SessionManager
from mas_server.types import AgentDefinition
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from conftest import TestTlsPaths as TlsPaths


def management(redis: Redis, *, token: str | None = None) -> ManagementService:
    """Create a management reader using real Redis without a gRPC listener."""
    agents = {"worker": AgentDefinition("worker", ["qa"], {})}
    return ManagementService(
        settings=ManagementSettings(
            port=0, token=token, auth_mode="token" if token else "local"
        ),
        redis=redis,
        sessions=SessionManager(agents=agents, redis=redis),
        agents=agents,
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "::"])
def test_remote_management_requires_authentication(host: str) -> None:
    with pytest.raises(ValueError, match="OIDC and HTTPS"):
        ManagementSettings(host=host)
    with pytest.raises(ValueError, match="OIDC and HTTPS"):
        ManagementSettings(host=host, auth_mode="token", token="secret")
    assert "secret" not in repr(ManagementSettings(auth_mode="token", token="secret"))


async def test_http_requires_token_for_operational_data(redis: Redis) -> None:
    service = management(redis, token="private-token")
    await service.start()
    try:
        async with ClientSession() as client:
            async with client.get(service.url) as response:
                assert response.status == 200
                assert "Control room" in await response.text()
            for route in ["/api/snapshot", "/healthz"]:
                async with client.get(service.url + route) as response:
                    assert response.status == 401
            async with client.get(
                service.url + "/api/snapshot",
                headers={"Authorization": "Bearer private-token"},
            ) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                text = await response.text()
                assert "private-token" not in text
                snapshot = ManagementSnapshot.model_validate_json(text)
                assert snapshot.health.status == "healthy"
                assert snapshot.agents[0].status == "inactive"
    finally:
        await service.stop()


async def test_snapshot_counts_pending_and_unconsumed_work(redis: Redis) -> None:
    stream = "agent.stream:worker"
    envelope = EnvelopeMessage(
        sender_id="sender", target_id="worker", message_type="ask", data={}
    )
    await redis.xadd(stream, {"envelope": envelope.model_dump_json()})
    await redis.xadd(stream, {"envelope": envelope.model_dump_json()})
    await redis.xgroup_create(stream, "agents", id="0-0")
    await redis.xreadgroup("agents", "instance", {stream: ">"}, count=1)
    service = management(redis)
    snapshot = await service.snapshot()
    assert snapshot.queues is not None
    assert snapshot.backlog == 2
    assert snapshot.queues[0].waiting == 1
    assert snapshot.queues[0].pending == 1
    assert snapshot.dead_letters == 0
    # Mutating a returned response cannot corrupt the shared cached response.
    snapshot.queues.clear()
    cached = await service.snapshot()
    assert cached.queues is not None and len(cached.queues) == 1


async def test_remote_broker_lease_is_active_without_local_instances(
    redis: Redis,
) -> None:
    service = management(redis)
    await service._sessions.leases.acquire("worker", "other-broker")
    snapshot = await service.snapshot()
    assert snapshot.agents[0].status == "active"
    assert snapshot.agents[0].sessions == []
    assert "health are local" in snapshot.scope


async def test_audit_metadata_is_typed_and_excludes_payload(redis: Redis) -> None:
    service = management(redis)
    audit = AuditModule(redis, file_sink=None)
    await audit.log_message(
        "first",
        "sender",
        "worker",
        "ALLOWED",
        1.5,
        {"secret": "payload-secret"},
        message_type="ask",
    )
    await audit.log_message(
        "second",
        "sender",
        "worker",
        "RATE_LIMITED",
        3.0,
        {},
        violations=["rate_limit_exceeded"],
    )
    snapshot = await service.snapshot()
    assert snapshot.health.status == "healthy"
    assert snapshot.recent_activity is not None
    assert [a.message_id for a in snapshot.recent_activity] == ["second", "first"]
    assert snapshot.recent_activity[1].latency_ms == 1.5
    assert "payload-secret" not in snapshot.model_dump_json()
    assert "payload_hash" not in snapshot.model_dump_json()


async def test_redis_outage_returns_unknown_data_and_503(
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = management(redis)
    monkeypatch.setattr(
        redis, "ping", AsyncMock(side_effect=ConnectionError("secret backend details"))
    )
    snapshot = await service.snapshot()
    assert snapshot.health.status == "degraded"
    assert snapshot.dead_letters is None
    assert snapshot.queues is None
    assert "secret backend details" not in snapshot.model_dump_json()
    await service.start()
    try:
        async with (
            ClientSession() as client,
            client.get(service.url + "/healthz") as response,
        ):
            assert response.status == 503
    finally:
        await service.stop()


async def test_snapshot_backend_failure_does_not_claim_empty_success(
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = management(redis)
    monkeypatch.setattr(
        redis, "xlen", AsyncMock(side_effect=ConnectionError("unavailable"))
    )
    snapshot = await service.snapshot()
    assert snapshot.health.status == "degraded"
    assert snapshot.dead_letters is None


async def test_failed_audit_read_remains_unknown(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = management(redis)
    monkeypatch.setattr(
        service._audit,
        "query_recent",
        AsyncMock(side_effect=ConnectionError("private backend details")),
    )
    snapshot = await service.snapshot()
    assert snapshot.health.status == "degraded"
    assert snapshot.recent_activity is None
    assert snapshot.queues == []
    assert snapshot.dead_letters == 0
    assert "private backend details" not in snapshot.model_dump_json()


async def test_server_exposes_and_closes_dashboard(test_tls: TlsPaths) -> None:
    settings = replace(
        dev_server_settings(agents={}, tls=test_tls.bundle, listen_addr="127.0.0.1:0"),
        management=ManagementSettings(port=0),
    )
    server = MASServer(settings=settings, gateway=GatewaySettings())
    await server.start()
    url = server.management_url
    try:
        async with ClientSession() as client, client.get(url + "/healthz") as response:
            assert response.status == 200
    finally:
        await server.stop()
    with pytest.raises(RuntimeError, match="not enabled or started"):
        _ = server.management_url
    # Lifecycle may be resumed on the same object without leaking listeners.
    await server.start()
    await server.stop()
    await server.stop()
    assert not server._running


async def test_stopped_worker_degrades_readiness(redis: Redis) -> None:
    service = management(redis)

    async def worker() -> None:
        return

    task = asyncio.create_task(worker())
    await task
    # Simulate a worker that ended while the transport still holds its session.
    session = await service._sessions.connect(
        agent_id="worker",
        instance_id="one",
        task_factory=lambda *_: task,
    )
    await session.task
    assert (await service.health()).status == "degraded"


@pytest.mark.parametrize(
    "token", ["bad\r\ntoken", "bad\x00token", "bad token", "", "é"]
)
def test_management_rejects_unusable_http_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="printable ASCII"):
        ManagementSettings(auth_mode="token", token=token)


async def test_unicode_authorization_is_unauthorized(redis: Redis) -> None:
    service = management(redis, token="secret")
    await service.start()
    try:
        async with (
            ClientSession() as client,
            client.get(
                service.url + "/api/snapshot",
                headers={"Authorization": "Bearer é"},
            ) as response,
        ):
            assert response.status == 401
    finally:
        await service.stop()


async def test_repeated_management_start_keeps_one_listener(redis: Redis) -> None:
    service = management(redis)
    await asyncio.gather(service.start(), service.start())
    url = service.url
    await service.start()
    assert service.url == url
    await service.stop()
    async with ClientSession() as client:
        with pytest.raises(OSError):
            await client.get(url)


async def test_duplicate_scan_results_do_not_inflate_backlog(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = management(redis)
    await redis.xadd("agent.stream:worker", {"envelope": "pending"})

    async def scan_iter(*args: object, **kwargs: object) -> AsyncIterator[str]:
        yield "agent.stream:worker"
        yield "agent.stream:worker"

    monkeypatch.setattr(redis, "scan_iter", scan_iter)
    snapshot = await service.snapshot()
    assert snapshot.queues is not None and len(snapshot.queues) == 1
    assert snapshot.backlog == 1


async def test_circuit_listing_is_bounded_and_read_only(redis: Redis) -> None:
    circuit = CircuitBreakerModule(redis, config=CircuitBreakerConfig())
    for name in ["one", "two", "three"]:
        await redis.hset(f"circuit:{name}", key="state", value="open")
    assert len(await circuit.get_all_circuits(limit=2)) == 2
    assert await redis.hget("circuit:one", "state") == "open"
    with pytest.raises(ValueError, match="positive"):
        await circuit.get_all_circuits(limit=0)
