from __future__ import annotations

import asyncio
import time

import grpc
import pytest

from mas import Agent
from mas.gateway.audit import AuditModule
from mas.gateway.config import (
    FeaturesSettings,
    GatewaySettings,
    RateLimitSettings,
    RedisSettings,
)
from mas.server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

pytestmark = pytest.mark.asyncio


async def _start_server(
    *,
    test_tls,
    agents: dict[str, AgentDefinition],
    gateway: GatewaySettings,
) -> MASServer:
    settings = MASServerSettings(
        redis_url=gateway.redis.url,
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path=test_tls.server_cert,
            server_key_path=test_tls.server_key,
            client_ca_path=test_tls.ca_pem,
        ),
        agents=agents,
    )
    server = MASServer(settings=settings, gateway=gateway)
    await server.start()
    return server


async def test_dlp_blocks_sensitive_payload(test_tls) -> None:
    gateway = GatewaySettings(
        redis=RedisSettings(url="redis://localhost:6379"),
        features=FeaturesSettings(dlp=True, rbac=False, circuit_breaker=False),
    )
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    server = await _start_server(test_tls=test_tls, agents=agents, gateway=gateway)
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    worker = Agent(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await worker.start()
    await sender.start()
    try:
        with pytest.raises(grpc.aio.AioRpcError) as excinfo:
            await sender.send(
                "worker",
                "payload",
                {"card": "4111 1111 1111 1111"},
            )
        assert excinfo.value.code() == grpc.StatusCode.PERMISSION_DENIED
    finally:
        await sender.stop()
        await worker.stop()
        await server.stop()


async def test_rate_limit_blocks_excess_messages(test_tls) -> None:
    gateway = GatewaySettings(
        redis=RedisSettings(url="redis://localhost:6379"),
        rate_limit=RateLimitSettings(per_minute=1, per_hour=1),
        features=FeaturesSettings(dlp=False, rbac=False, circuit_breaker=False),
    )
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    server = await _start_server(test_tls=test_tls, agents=agents, gateway=gateway)
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    worker = Agent(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await worker.start()
    await sender.start()
    try:
        await sender.send("worker", "payload", {"ok": True})
        with pytest.raises(grpc.aio.AioRpcError) as excinfo:
            await sender.send("worker", "payload", {"ok": False})
        assert excinfo.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    finally:
        await sender.stop()
        await worker.stop()
        await server.stop()


async def test_dlp_alert_logged(test_tls, redis) -> None:
    gateway = GatewaySettings(
        redis=RedisSettings(url="redis://localhost:6379"),
        features=FeaturesSettings(dlp=True, rbac=False, circuit_breaker=False),
    )
    agents = {
        "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
    }
    server = await _start_server(test_tls=test_tls, agents=agents, gateway=gateway)
    await server.authz.set_permissions("sender", allowed_targets=["worker"])

    worker = Agent(
        "worker",
        server_addr=server.bound_addr,
        tls=test_tls.client("worker"),
    )
    sender = Agent(
        "sender",
        server_addr=server.bound_addr,
        tls=test_tls.client("sender"),
    )

    await worker.start()
    await sender.start()
    try:
        await sender.send(
            "worker",
            "payload",
            {"text": "Contact me at user@example.com"},
        )

        audit = AuditModule(redis)
        deadline = time.monotonic() + 2.0
        entries = []
        while time.monotonic() < deadline:
            entries = await audit.query_by_decision("ALERT")
            if entries:
                break
            await asyncio.sleep(0.05)

        assert entries, "Expected ALERT audit entry for DLP email detection"
        assert any("email" in entry.get("violations", []) for entry in entries)
    finally:
        await sender.stop()
        await worker.stop()
        await server.stop()
