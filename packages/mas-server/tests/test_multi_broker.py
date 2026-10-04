"""Two-broker ownership, crash expiry, and stale-owner fencing regressions."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import grpc
import grpc.aio as grpc_aio
import pytest
from mas_agent import Agent
from mas_core import EnvelopeMessage
from mas_core.sessions import SessionLeaseSettings, SessionLeaseStore
from mas_proto.runtime.v1 import runtime_pb2 as pb
from mas_proto.runtime.v1 import runtime_pb2_grpc as rpc
from mas_server.delivery import DeliveryService
from mas_server.errors import FailedPreconditionError
from mas_server.routing import MessageRouter
from mas_server.runtime import MASServer
from mas_server.sessions import SessionManager
from mas_server.types import (
    AgentDefinition,
    InflightDelivery,
    MASServerSettings,
    TlsConfig,
)
from redis.asyncio import Redis

from conftest import TestTlsPaths as TlsPaths

pytestmark = pytest.mark.asyncio
ServerFactory = Callable[[dict[str, AgentDefinition] | None], Awaitable[MASServer]]


def _channel(server: MASServer, tls: TlsPaths, agent_id: str) -> grpc_aio.Channel:
    credentials = tls.client(agent_id)
    return grpc_aio.secure_channel(
        server.bound_addr,
        grpc.ssl_channel_credentials(
            root_certificates=Path(credentials.root_ca_path).read_bytes(),
            private_key=Path(credentials.client_key_path).read_bytes(),
            certificate_chain=Path(credentials.client_cert_path).read_bytes(),
        ),
    )


async def test_peer_start_disconnect_and_unary_routing_preserve_live_sessions(
    redis: Redis,
    mas_server_factory: ServerFactory,
    test_tls: TlsPaths,
) -> None:
    agents = {aid: AgentDefinition(aid, [], {}) for aid in ("sender", "worker")}
    first = await mas_server_factory(agents)
    sender = Agent(
        "sender", server_addr=first.bound_addr, tls=test_tls.client("sender")
    )
    received = asyncio.Event()

    class Worker(Agent):
        async def on_message(self, message: EnvelopeMessage) -> None:
            received.set()

    worker = Worker(
        "worker", server_addr=first.bound_addr, tls=test_tls.client("worker")
    )
    await first.authz.set_permissions("sender", allowed_targets=["worker"])
    await worker.start()
    await sender.start()
    try:
        second = await mas_server_factory(agents)
        assert [
            row["id"]
            for row in await second.discover(agent_id="sender", capabilities=[])
        ] == ["worker"]
        with pytest.raises(FailedPreconditionError, match="instance_already_connected"):
            await second.connect_session(
                agent_id="sender", instance_id=sender.instance_id
            )
        await second.connect_session(agent_id="worker", instance_id="peer")
        await second.disconnect_session(agent_id="worker", instance_id="peer")
        assert [
            row["id"]
            for row in await second.discover(agent_id="sender", capabilities=[])
        ] == ["worker"]
        async with _channel(second, test_tls, "sender") as channel:
            response = await rpc.RuntimeServiceStub(channel).Send(
                pb.SendRequest(
                    instance_id=sender.instance_id,
                    target_id="worker",
                    message_type="work",
                    data_json="{}",
                ),
                timeout=2,
            )
            assert response.message_id
        await asyncio.wait_for(received.wait(), timeout=2)
        await second.stop()
        assert [
            row["id"]
            for row in await first.discover(agent_id="sender", capabilities=[])
        ] == ["worker"]
    finally:
        await sender.stop()
        await worker.stop()


async def test_renewal_loss_closes_live_transport(
    redis: Redis,
    mas_server_factory: ServerFactory,
    test_tls: TlsPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = await mas_server_factory({"worker": AgentDefinition("worker", [], {})})
    assert server._sessions is not None
    server._sessions.leases.settings = SessionLeaseSettings(ttl_ms=150, renewal_ms=40)
    monkeypatch.setattr(server._sessions.leases, "renew", AsyncMock(return_value=None))
    async with _channel(server, test_tls, "worker") as channel:
        call = rpc.RuntimeServiceStub(channel).Transport()
        await call.write(pb.ClientEvent(hello=pb.Hello(instance_id="lease-loss")))
        assert (await call.read()).HasField("welcome")
        with pytest.raises(grpc_aio.AioRpcError) as error:
            await asyncio.wait_for(call.read(), timeout=2)
        assert error.value.code() == grpc.StatusCode.UNAVAILABLE
        assert error.value.details() == "session_lease_lost"
    assert await server._sessions.leases.owner("worker", "lease-loss") is None


async def test_expired_owner_cannot_renew_release_or_ack_successor_work(
    redis: Redis,
    test_tls: TlsPaths,
) -> None:
    leases = SessionLeaseStore(redis, SessionLeaseSettings(ttl_ms=150, renewal_ms=40))
    old = await leases.acquire("worker", "reused")
    await redis.pexpire("mas.session:worker:reused", 1)
    await asyncio.sleep(0.01)
    current = await leases.acquire("worker", "reused")
    assert not await leases.renew(old)
    await leases.release(old)
    assert await leases.owner("worker", "reused") == current.owner
    stream = "agent.stream:worker"
    await redis.xgroup_create(stream, "agents", id="0-0", mkstream=True)
    entry = await redis.xadd(stream, {"envelope": "{}"})
    assert isinstance(entry, str)
    await redis.xreadgroup("agents", "successor", {stream: ">"})
    sessions = SessionManager(agents={}, redis=redis)
    service = DeliveryService(
        redis=redis,
        settings=MASServerSettings(
            listen_addr="unused:0",
            tls=TlsConfig(test_tls.server_cert, test_tls.server_key, test_tls.ca_pem),
            agents={},
        ),
        sessions=sessions,
        router=MessageRouter(redis=redis, dlq_enabled=True),
        circuit_breaker=None,
    )
    stale = InflightDelivery(stream, "agents", entry, "{}", 0)
    assert not await service._ack_inflight(stale, consumer="successor", lease=old)
    assert (await redis.xpending(stream, "agents"))["pending"] == 1
    assert await redis.xlen(stream) == 1
    await leases.release(current)


async def test_local_expiry_cannot_be_extended_even_if_redis_still_has_owner(
    redis: Redis,
) -> None:
    leases = SessionLeaseStore(redis)
    lease = await leases.acquire("worker", "local-expiry")
    expired = replace(lease, expires_at=time.monotonic() - 1)
    assert not expired.live
    assert await leases.renew(expired) is None
    assert await leases.owner("worker", "local-expiry") == lease.owner
    await leases.release(lease)


async def test_expired_local_session_cannot_borrow_persisted_owner(
    redis: Redis,
    mas_server_factory: ServerFactory,
) -> None:
    server = await mas_server_factory({"worker": AgentDefinition("worker", [], {})})
    session = await server.connect_session(agent_id="worker", instance_id="local")
    sessions = server._require_sessions()
    assert await sessions.leases.owner("worker", "local") == session.lease.owner
    await sessions.ensure_connected("worker", "local")
    session.lease = replace(session.lease, expires_at=time.monotonic() - 1)
    with pytest.raises(FailedPreconditionError, match="session_not_connected"):
        await sessions.ensure_connected("worker", "local")
    assert await sessions.leases.owner("worker", "local") == session.lease.owner


_CRASH_OWNER = """
import asyncio, sys
from mas_core.sessions import SessionLeaseSettings
from mas_gateway.config import GatewaySettings
from mas_server.runtime import MASServer
from mas_server.types import AgentDefinition, MASServerSettings, TlsConfig
async def main():
    server = MASServer(settings=MASServerSettings(listen_addr='127.0.0.1:0',
        tls=TlsConfig(*sys.argv[1:4]),
        agents={'worker':AgentDefinition('worker',[],{})},
        session_lease=SessionLeaseSettings(ttl_ms=300, renewal_ms=80)),
        gateway=GatewaySettings())
    await server.start()
    await server.connect_session(agent_id='worker', instance_id='crashed')
    print('READY', flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


async def test_killed_broker_lease_expires_and_peer_takes_ownership(
    redis: Redis,
    mas_server_factory: ServerFactory,
    test_tls: TlsPaths,
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CRASH_OWNER,
        test_tls.server_cert,
        test_tls.server_key,
        test_tls.ca_pem,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"READY\n"
        peer = await mas_server_factory({"worker": AgentDefinition("worker", [], {})})
        with pytest.raises(FailedPreconditionError, match="instance_already_connected"):
            await peer.connect_session(agent_id="worker", instance_id="crashed")
        process.kill()
        await process.wait()
        await asyncio.sleep(0.35)
        session = await peer.connect_session(agent_id="worker", instance_id="crashed")
        assert session.lease.owner == await peer._require_sessions().leases.owner(
            "worker", "crashed"
        )
        await peer.disconnect_session(agent_id="worker", instance_id="crashed")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
