"""Wire and Redis regressions for bounded transport ACK processing."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import grpc
import grpc.aio as grpc_aio
import pytest
import pytest_asyncio
from mas_core import get_telemetry
from mas_core.sessions import SessionLease
from mas_gateway.config import GatewaySettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.delivery import _READ_SCRIPT, DeliveryService
from mas_server.errors import InvalidArgumentError, RpcError
from mas_server.routing import DeliveryCommit, MessageRouter
from mas_server.runtime import MASServer
from mas_server.servicer import MasGrpcServicer
from mas_server.sessions import SessionManager
from mas_server.types import (
    AgentDefinition,
    InflightDelivery,
    MASServerSettings,
    OutboundDelivery,
    Session,
    TlsConfig,
)
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

pytestmark = pytest.mark.asyncio


@dataclass(slots=True)
class AckTransport:
    """Owned wire connection and live lease backing each regression."""

    runtime: MASServer
    session: Session
    delivery: DeliveryService
    channel: grpc_aio.Channel


@dataclass(slots=True)
class AckProbe:
    """Count actual storage acknowledgements waiting at a deterministic barrier."""

    active: int = 0
    maximum: int = 0
    completed: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


async def _idle() -> None:
    await asyncio.Event().wait()


def _task(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[OutboundDelivery],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle())


async def _identity(
    _context: grpc_aio.ServicerContext, *, tls: TlsConfig | None = None
) -> str:
    return "worker"


@pytest_asyncio.fixture
async def ack_transport(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AckTransport]:
    agents = {"worker": AgentDefinition("worker", [], {})}
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig("unused.pem", "unused.key", "unused-ca.pem"),
        agents=agents,
        max_in_flight=4,
    )
    runtime = MASServer(settings=settings, gateway=GatewaySettings())
    sessions = SessionManager(agents=agents, redis=redis)
    session = await sessions.connect(
        agent_id="worker", instance_id="instance", task_factory=_task
    )
    delivery = DeliveryService(
        redis=redis,
        settings=settings,
        sessions=sessions,
        router=MessageRouter(redis=redis, dlq_enabled=False),
        circuit_breaker=None,
    )
    runtime._sessions = sessions
    runtime._delivery = delivery

    async def connect(*, agent_id: str, instance_id: str) -> Session:
        assert (agent_id, instance_id) == ("worker", "instance")
        return session

    monkeypatch.setattr(runtime, "connect_session", connect)
    monkeypatch.setattr("mas_server.servicer.spiffe_agent_id", _identity)
    server = grpc_aio.server()
    mas_pb2_grpc.add_RuntimeServiceServicer_to_server(MasGrpcServicer(runtime), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc_aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield AckTransport(runtime, session, delivery, channel)
    finally:
        await server.stop(grace=0)
        await sessions.disconnect(agent_id="worker", instance_id="instance")


async def _pending_entries(
    redis: Redis, transport: AckTransport, count: int
) -> list[str]:
    stream = "agent.stream:worker"
    await redis.xgroup_create(stream, "agents", id="0-0", mkstream=True)
    consumer = f"worker-instance-{transport.session.lease.owner}"
    identifiers: list[str] = []
    for index in range(count):
        entry = TypeAdapter(str).validate_python(
            await redis.xadd(stream, {"envelope": "{}"})
        )
        delivery_id = f"delivery-{index}"
        identifiers.append(delivery_id)
        transport.session.inflight[delivery_id] = InflightDelivery(
            stream_name=stream,
            group="agents",
            entry_id=entry,
            envelope_json="{}",
            received_at=time.monotonic(),
            consumer=consumer,
        )
    await redis.xreadgroup("agents", consumer, streams={stream: ">"}, count=count)
    return identifiers


@pytest.mark.parametrize("replace_owner", [False, True])
async def test_ack_storage_runs_concurrently_is_bounded_and_keeps_owner_fence(
    redis: Redis,
    ack_transport: AckTransport,
    monkeypatch: pytest.MonkeyPatch,
    replace_owner: bool,
) -> None:
    transport = ack_transport
    identifiers = await _pending_entries(redis, transport, 8)
    original = transport.delivery._ack_inflight
    probe = AckProbe()

    async def acknowledge(
        inflight: InflightDelivery, *, consumer: str, lease: SessionLease
    ) -> bool:
        probe.active += 1
        probe.maximum = max(probe.maximum, probe.active)
        if probe.active == 4:
            probe.entered.set()
        try:
            await probe.release.wait()
            acknowledged = await original(inflight, consumer=consumer, lease=lease)
            probe.completed += 1
            return acknowledged
        finally:
            probe.active -= 1

    monkeypatch.setattr(transport.delivery, "_ack_inflight", acknowledge)
    call = transport.channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
    await call.write(
        mas_pb2.ClientEvent(
            hello=mas_pb2.Hello(instance_id="instance")
        ).SerializeToString()
    )
    welcome = await call.read()
    assert isinstance(welcome, bytes)
    for delivery_id in identifiers:
        await call.write(
            mas_pb2.ClientEvent(
                ack=mas_pb2.Ack(delivery_id=delivery_id)
            ).SerializeToString()
        )
    await call.done_writing()
    async with asyncio.timeout(2):
        await probe.entered.wait()
    assert probe.active == 4
    if replace_owner:
        await redis.set("mas.session:worker:instance", "successor", px=6000)
    probe.release.set()
    assert await asyncio.wait_for(call.read(), timeout=2) == grpc_aio.EOF
    assert probe.maximum == 4
    assert probe.completed == 8
    pending = await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10)
    assert len(pending) == (8 if replace_owner else 0)
    assert await redis.xlen("agent.stream:worker") == (8 if replace_owner else 0)


@pytest.mark.parametrize(
    ("error", "code", "details"),
    [
        (
            RedisConnectionError("redis://private-host/backend-secret"),
            grpc.StatusCode.UNAVAILABLE,
            "storage_unavailable",
        ),
        (
            InvalidArgumentError("invalid_delivery_id"),
            grpc.StatusCode.INVALID_ARGUMENT,
            "invalid_delivery_id",
        ),
    ],
)
async def test_ack_worker_error_keeps_sanitized_storage_and_domain_status(
    ack_transport: AckTransport,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: grpc.StatusCode,
    details: str,
) -> None:
    transport = ack_transport
    redis_errors = get_telemetry().snapshot().redis_errors

    async def acknowledge(*, agent_id: str, instance_id: str, delivery_id: str) -> None:
        raise error

    monkeypatch.setattr(transport.runtime, "handle_ack", acknowledge)
    call = transport.channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
    await call.write(
        mas_pb2.ClientEvent(
            hello=mas_pb2.Hello(instance_id="instance")
        ).SerializeToString()
    )
    welcome = await call.read()
    assert isinstance(welcome, bytes)
    await call.write(
        mas_pb2.ClientEvent(ack=mas_pb2.Ack(delivery_id="failed")).SerializeToString()
    )
    with pytest.raises(grpc_aio.AioRpcError) as raised:
        await asyncio.wait_for(call.read(), timeout=2)
    assert raised.value.code() == code
    assert raised.value.details() == details
    assert get_telemetry().snapshot().redis_errors == redis_errors + (
        1 if isinstance(error, RedisError) else 0
    )


async def test_forced_disconnect_cancels_all_ack_workers(
    ack_transport: AckTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = ack_transport
    probe = AckProbe()

    async def acknowledge(*, agent_id: str, instance_id: str, delivery_id: str) -> None:
        probe.active += 1
        if probe.active == 4:
            probe.entered.set()
        try:
            await probe.release.wait()
        finally:
            probe.active -= 1

    monkeypatch.setattr(transport.runtime, "handle_ack", acknowledge)
    call = transport.channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
    await call.write(
        mas_pb2.ClientEvent(
            hello=mas_pb2.Hello(instance_id="instance")
        ).SerializeToString()
    )
    welcome = await call.read()
    assert isinstance(welcome, bytes)
    for index in range(4):
        await call.write(
            mas_pb2.ClientEvent(
                ack=mas_pb2.Ack(delivery_id=f"delivery-{index}")
            ).SerializeToString()
        )
    async with asyncio.timeout(2):
        await probe.entered.wait()
    call.cancel()
    async with asyncio.timeout(2):
        while probe.active or await transport.runtime._require_sessions().snapshot():
            await asyncio.sleep(0.01)
    assert probe.active == 0


async def test_actual_ack_storage_failure_aborts_transport_without_deleting_pending(
    redis: Redis,
    ack_transport: AckTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ack_transport
    identifiers = await _pending_entries(redis, transport, 1)
    original = redis.eval

    async def execute(script: str, numkeys: int, *values: str | int) -> object:
        if "local acknowledged = redis.call('XACK'" in script:
            raise RedisConnectionError("redis://private-backend/secret")
        return await original(script, numkeys, *values)

    monkeypatch.setattr(redis, "eval", execute)
    call = transport.channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
    await call.write(
        mas_pb2.ClientEvent(
            hello=mas_pb2.Hello(instance_id="instance")
        ).SerializeToString()
    )
    welcome = await call.read()
    assert isinstance(welcome, bytes)
    await call.write(
        mas_pb2.ClientEvent(
            ack=mas_pb2.Ack(delivery_id=identifiers[0])
        ).SerializeToString()
    )
    with pytest.raises(grpc_aio.AioRpcError) as raised:
        await asyncio.wait_for(call.read(), timeout=2)
    assert raised.value.code() == grpc.StatusCode.UNAVAILABLE
    assert raised.value.details() == "storage_unavailable"
    assert (
        len(await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10))
        == 1
    )
    assert await redis.xlen("agent.stream:worker") == 1


@pytest.mark.parametrize("replacement", ["lease", "consumer", "none"])
async def test_terminal_nack_atomically_fences_dlq_and_original_delivery(
    redis: Redis,
    ack_transport: AckTransport,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    transport = ack_transport
    identifiers = await _pending_entries(redis, transport, 1)
    router = MessageRouter(redis=redis, dlq_enabled=True)
    transport.delivery._router = router
    original = router.write_dlq
    entered = asyncio.Event()
    release = asyncio.Event()

    async def commit(
        *, envelope_json: str, reason: str, delivery: DeliveryCommit | None = None
    ) -> bool:
        entered.set()
        await release.wait()
        return await original(
            envelope_json=envelope_json, reason=reason, delivery=delivery
        )

    monkeypatch.setattr(router, "write_dlq", commit)
    task = asyncio.create_task(
        transport.delivery.handle_nack(
            agent_id="worker",
            instance_id="instance",
            delivery_id=identifiers[0],
            reason="permanent",
            retryable=False,
        )
    )
    async with asyncio.timeout(2):
        await entered.wait()
    if replacement == "lease":
        await redis.set("mas.session:worker:instance", "successor", px=6000)
    elif replacement == "consumer":
        entries = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("agent.stream:worker")
        )
        await redis.xclaim(
            "agent.stream:worker", "agents", "successor", 0, [entries[0][0]]
        )
    release.set()
    await asyncio.wait_for(task, timeout=2)
    stale = replacement != "none"
    assert await redis.xlen("dlq:messages") == (0 if stale else 1)
    assert await redis.xlen("agent.stream:worker") == (1 if stale else 0)
    pending = await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10)
    assert len(pending) == (1 if stale else 0)


@pytest.mark.parametrize("replace_owner", [False, True])
async def test_invalid_attempt_uses_the_same_fenced_terminal_transition(
    redis: Redis, ack_transport: AckTransport, replace_owner: bool
) -> None:
    transport = ack_transport
    identifiers = await _pending_entries(redis, transport, 1)
    pending = transport.session.inflight[identifiers[0]]
    transport.delivery._router = MessageRouter(redis=redis, dlq_enabled=True)
    if replace_owner:
        await redis.set("mas.session:worker:instance", "successor", px=6000)
    await transport.delivery._deliver_entry(
        agent_id="worker",
        instance_id="instance",
        outbound=transport.session.outbound,
        inflight=transport.session.inflight,
        stream_name=pending.stream_name,
        group=pending.group,
        entry_id=pending.entry_id,
        envelope_json=pending.envelope_json,
        attempt_text="invalid",
    )
    assert await redis.xlen("dlq:messages") == (0 if replace_owner else 1)
    assert await redis.xlen("agent.stream:worker") == (1 if replace_owner else 0)


@pytest.mark.parametrize("expired", [False, True])
async def test_fenced_read_does_not_create_pending_work_after_owner_loss(
    redis: Redis,
    ack_transport: AckTransport,
    monkeypatch: pytest.MonkeyPatch,
    expired: bool,
) -> None:
    transport = ack_transport
    original = redis.eval
    lease_key = "mas.session:worker:instance"
    await redis.xadd("agent.stream:worker", {"envelope": "{}"})

    async def execute(script: str, numkeys: int, *values: str | int) -> object:
        if script == _READ_SCRIPT:
            if expired:
                await redis.pexpire(lease_key, 0)
            else:
                await redis.set(lease_key, "successor", px=6000)
        return await original(script, numkeys, *values)

    monkeypatch.setattr(redis, "eval", execute)
    transport.delivery.set_running(True)
    with pytest.raises(RpcError, match="session_lease_lost"):
        await transport.delivery._stream_loop(
            agent_id="worker",
            instance_id="instance",
            outbound=transport.session.outbound,
            inflight=transport.session.inflight,
        )
    assert not transport.session.inflight
    assert transport.session.outbound.empty()
    assert not await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10)
    assert await redis.xlen("agent.stream:worker") == 1


async def test_idle_fenced_reader_delivers_new_work_within_100_ms(
    redis: Redis, ack_transport: AckTransport
) -> None:
    transport = ack_transport
    transport.delivery.set_running(True)
    task = transport.delivery.start_stream_task(
        "worker", "instance", transport.session.outbound, transport.session.inflight
    )
    try:
        await asyncio.sleep(0.2)
        assert transport.session.outbound.empty()
        started = time.monotonic()
        await redis.xadd("agent.stream:worker", {"envelope": "{}"})
        event = await asyncio.wait_for(transport.session.outbound.get(), timeout=0.15)
        assert event.delivery.envelope_json == "{}"
        assert time.monotonic() - started < 0.1
    finally:
        transport.delivery.set_running(False)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
