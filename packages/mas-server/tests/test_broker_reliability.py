"""Regression coverage for durable delivery and request/reply contracts."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock

import grpc
import grpc.aio as grpc_aio
import pytest
import pytest_asyncio
from mas_core import EnvelopeMessage, JsonObject
from mas_gateway import AuditModule, AuthorizationModule, RateLimitModule
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.delivery import _READ_SCRIPT, DeliveryService
from mas_server.errors import InvalidArgumentError, PermissionDeniedError
from mas_server.ingress import IngressService
from mas_server.policy import PolicyPipeline
from mas_server.registry import RegistryService
from mas_server.routing import MessageRouter
from mas_server.runtime import MASServer
from mas_server.sessions import SessionManager
from mas_server.types import (
    AgentDefinition,
    InflightDelivery,
    MASServerSettings,
    OutboundDelivery,
    TlsConfig,
)
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from conftest import TestTlsPaths as TlsFixture

pytestmark = pytest.mark.asyncio


async def _idle_task() -> None:
    await asyncio.Event().wait()


def _task_factory(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[OutboundDelivery],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_task())


@pytest_asyncio.fixture
async def sessions(redis: Redis) -> AsyncGenerator[SessionManager]:
    manager = SessionManager(
        redis=redis,
        agents={
            agent_id: AgentDefinition(agent_id=agent_id, capabilities=[], metadata={})
            for agent_id in ("requester", "responder", "worker")
        },
    )
    for agent_id in ("requester", "responder", "worker"):
        await manager.connect(
            agent_id=agent_id,
            instance_id=f"{agent_id}-inst",
            task_factory=_task_factory,
        )
    try:
        yield manager
    finally:
        active = await manager.snapshot_and_clear()
        for session in active:
            session.task.cancel()
        await asyncio.gather(
            *(session.task for session in active), return_exceptions=True
        )


def _delivery(
    redis: Redis,
    *,
    max_in_flight: int = 2,
    sessions: SessionManager | None = None,
) -> DeliveryService:
    return DeliveryService(
        redis=redis,
        settings=MASServerSettings(
            listen_addr="127.0.0.1:0",
            tls=TlsConfig(
                server_cert_path="server.pem",
                server_key_path="server.key",
                client_ca_path="ca.pem",
            ),
            agents={},
            reclaim_idle_ms=1,
            max_in_flight=max_in_flight,
        ),
        sessions=sessions
        if sessions is not None
        else SessionManager(agents={}, redis=redis),
        router=MessageRouter(redis=redis, dlq_enabled=True),
        circuit_breaker=None,
    )


def _policy(redis: Redis) -> PolicyPipeline:
    return PolicyPipeline(
        authz=AuthorizationModule(redis, enable_rbac=False),
        rate_limit=RateLimitModule(
            redis, default_per_minute=100, default_per_hour=1000
        ),
        audit=AuditModule(redis, file_sink=None),
        router=MessageRouter(redis=redis, dlq_enabled=True),
        dlp=None,
        circuit_breaker=None,
    )


async def test_group_delivers_entries_queued_before_first_connection(
    redis: Redis,
) -> None:
    service = _delivery(redis)
    entry_id = await redis.xadd("agent.stream:worker", {"envelope": "{}"})

    await service._ensure_group_exists(
        stream_name="agent.stream:worker", group="agents"
    )
    items = await redis.xreadgroup("agents", "consumer", {"agent.stream:worker": ">"})

    assert items
    pending = await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 1)
    assert pending[0]["message_id"] == entry_id
    await service._ensure_group_exists(
        stream_name="agent.stream:worker", group="agents"
    )


@pytest.mark.parametrize("max_in_flight", [1, 2, 3])
async def test_read_budget_applies_across_both_streams(
    redis: Redis,
    sessions: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
    max_in_flight: int,
) -> None:
    service = _delivery(redis, max_in_flight=max_in_flight, sessions=sessions)
    stream_names = ("agent.stream:worker", "agent.stream:worker:worker-inst")
    for stream_name in stream_names:
        for _index in range(3):
            await redis.xadd(stream_name, {"envelope": "{}"})
    original_read = redis.eval

    async def read_once(script: str, numkeys: int, *values: str | int) -> object:
        if script == _READ_SCRIPT:
            service.set_running(False)
        return await original_read(script, numkeys, *values)

    monkeypatch.setattr(redis, "eval", read_once)
    outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue()
    inflight: dict[str, InflightDelivery] = {}
    service.set_running(True)
    await service._stream_loop(
        agent_id="worker",
        instance_id="worker-inst",
        outbound=outbound,
        inflight=inflight,
    )

    pending = [await redis.xpending(stream, "agents") for stream in stream_names]
    assert sum(info["pending"] for info in pending) == len(inflight)
    assert len(inflight) <= max_in_flight
    assert len(inflight) == outbound.qsize()


async def test_reclaim_respects_capacity_and_does_not_duplicate_local_inflight(
    redis: Redis,
    sessions: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _delivery(redis, max_in_flight=3, sessions=sessions)
    stream = "agent.stream:worker"
    await service._ensure_group_exists(stream_name=stream, group="agents")
    for _index in range(5):
        await redis.xadd(stream, {"envelope": "{}"})
    await redis.xreadgroup("agents", "old-consumer", {stream: ">"}, count=5)
    await asyncio.sleep(0.01)
    outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue(maxsize=2)
    inflight: dict[str, InflightDelivery] = {}
    await service._reclaim_pending(
        stream,
        "agents",
        "worker-worker-inst",
        "0-0",
        agent_id="worker",
        instance_id="worker-inst",
        outbound=outbound,
        inflight=inflight,
    )
    assert len(inflight) == outbound.qsize() == 2
    first = next(iter(inflight.values()))
    outbound.get_nowait()
    inflight_ids = set(inflight)
    claim = AsyncMock(
        return_value=[
            "0-0",
            [(first.entry_id, ["envelope", first.envelope_json])],
            [],
        ]
    )
    with monkeypatch.context() as patch:
        patch.setattr(redis, "eval", claim)
        await service._reclaim_pending(
            stream,
            "agents",
            "worker-worker-inst",
            "0-0",
            agent_id="worker",
            instance_id="worker-inst",
            outbound=outbound,
            inflight=inflight,
        )
    claim.assert_awaited_once()
    assert set(inflight) == inflight_ids
    assert outbound.qsize() == 1


@pytest.mark.parametrize("outcome", ["ack", "retry", "terminal", "exhausted"])
async def test_completed_entries_are_deleted_after_durable_outcome(
    redis: Redis, sessions: SessionManager, outcome: str
) -> None:
    service = _delivery(redis, sessions=sessions)
    session = next(s for s in await sessions.snapshot() if s.agent_id == "worker")
    stream = "agent.stream:worker"
    await service._ensure_group_exists(stream_name=stream, group="agents")
    envelope = EnvelopeMessage(
        sender_id="requester", target_id="worker", message_type="work", data={}
    ).model_dump_json()
    attempt = "5" if outcome == "exhausted" else "1"
    entry_id = await redis.xadd(stream, {"envelope": envelope, "attempt": attempt})
    assert isinstance(entry_id, str)
    await redis.xreadgroup(
        "agents", f"worker-worker-inst-{session.lease.owner}", {stream: ">"}
    )
    await service._deliver_entry(
        agent_id="worker",
        instance_id="worker-inst",
        outbound=session.outbound,
        inflight=session.inflight,
        stream_name=stream,
        group="agents",
        entry_id=entry_id,
        envelope_json=envelope,
        attempt_text=attempt,
    )
    event = await session.outbound.get()
    original = session.inflight[event.delivery.delivery_id]
    if outcome == "ack":
        await service.handle_ack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id=event.delivery.delivery_id,
        )
    else:
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id=event.delivery.delivery_id,
            reason="handler_failed",
            retryable=outcome != "terminal",
        )

    assert await redis.xrange(stream, min=entry_id, max=entry_id) == []
    assert (await redis.xpending(stream, "agents"))["pending"] == 0
    assert session.inflight == {}
    assert await redis.xlen(stream) == (1 if outcome == "retry" else 0)
    assert await redis.xlen("dlq:messages") == (
        1 if outcome in {"terminal", "exhausted"} else 0
    )
    if outcome == "retry":
        entries = await redis.xrange(stream)
        assert entries is not None
        _retry_id, fields = entries[0]
        assert fields is not None
        assert fields["attempt"] == "2"
        session.inflight["stale-delivery"] = original
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="stale-delivery",
            reason="retry",
            retryable=True,
        )
        assert await redis.xlen(stream) == 1


async def test_retry_script_failure_preserves_pending_original(
    redis: Redis, sessions: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _delivery(redis, sessions=sessions)
    session = next(s for s in await sessions.snapshot() if s.agent_id == "worker")
    stream = "agent.stream:worker"
    await service._ensure_group_exists(stream_name=stream, group="agents")
    entry_id = await redis.xadd(stream, {"envelope": "{}"})
    assert isinstance(entry_id, str)
    await redis.xreadgroup(
        "agents", f"worker-worker-inst-{session.lease.owner}", {stream: ">"}
    )
    session.inflight["delivery"] = InflightDelivery(
        stream_name=stream,
        group="agents",
        entry_id=entry_id,
        envelope_json="{}",
        received_at=time.time(),
        consumer=f"worker-worker-inst-{session.lease.owner}",
    )
    monkeypatch.setattr(
        redis, "eval", AsyncMock(side_effect=RedisConnectionError("unavailable"))
    )
    with pytest.raises(RedisConnectionError):
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="delivery",
            reason="retry",
            retryable=True,
        )
    assert await redis.xlen(stream) == 1
    assert (await redis.xpending(stream, "agents"))["pending"] == 1


@pytest.mark.parametrize("retry", [True, False])
async def test_stale_owner_cannot_ack_or_requeue_reclaimed_entry(
    redis: Redis, sessions: SessionManager, retry: bool
) -> None:
    service = _delivery(redis, sessions=sessions)
    session = next(s for s in await sessions.snapshot() if s.agent_id == "worker")
    stream = "agent.stream:worker"
    await service._ensure_group_exists(stream_name=stream, group="agents")
    entry_id = await redis.xadd(stream, {"envelope": "{}"})
    assert isinstance(entry_id, str)
    await redis.xreadgroup(
        "agents", f"worker-worker-inst-{session.lease.owner}", {stream: ">"}
    )
    session.inflight["old-delivery"] = InflightDelivery(
        stream_name=stream,
        group="agents",
        entry_id=entry_id,
        envelope_json="{}",
        received_at=time.time(),
        consumer=f"worker-worker-inst-{session.lease.owner}",
    )
    await redis.xclaim(stream, "agents", "new-consumer", 0, [entry_id])
    if retry:
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="old-delivery",
            reason="retry",
            retryable=True,
        )
    else:
        await service.handle_ack(
            agent_id="worker", instance_id="worker-inst", delivery_id="old-delivery"
        )
    assert await redis.xlen(stream) == 1
    pending = await redis.xpending_range(stream, "agents", entry_id, entry_id, 1)
    assert pending[0]["consumer"] == "new-consumer"


async def test_outbound_queue_capacity_limits_new_pending_entries(
    redis: Redis, sessions: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _delivery(redis, max_in_flight=200, sessions=sessions)
    await redis.xadd("agent.stream:worker", {"envelope": "{}"})
    await redis.xadd("agent.stream:worker:worker-inst", {"envelope": "{}"})
    original_read = redis.eval

    async def read_once(script: str, numkeys: int, *values: str | int) -> object:
        if script == _READ_SCRIPT:
            service.set_running(False)
        return await original_read(script, numkeys, *values)

    monkeypatch.setattr(redis, "eval", read_once)
    outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue(maxsize=1)
    inflight: dict[str, InflightDelivery] = {}
    service.set_running(True)
    await service._stream_loop(
        agent_id="worker",
        instance_id="worker-inst",
        outbound=outbound,
        inflight=inflight,
    )
    assert outbound.qsize() == len(inflight) == 1
    pending = [
        await redis.xpending(stream, "agents")
        for stream in ("agent.stream:worker", "agent.stream:worker:worker-inst")
    ]
    assert sum(info["pending"] for info in pending) == 1


async def test_malformed_dlq_failure_returns_false(redis: Redis) -> None:
    await redis.set("dlq:messages", "wrong-type")
    router = MessageRouter(redis=redis, dlq_enabled=True)
    assert await router.write_dlq(envelope_json="{", reason="malformed") is False


async def test_dlq_keeps_original_envelope_for_recovery(redis: Redis) -> None:
    envelope = EnvelopeMessage(
        sender_id="requester", target_id="worker", message_type="work", data={"task": 1}
    ).model_dump_json()
    router = MessageRouter(redis=redis, dlq_enabled=True)
    assert (
        await router.write_dlq(envelope_json=envelope, reason="handler_failed") is True
    )
    entries = await redis.xrange("dlq:messages")
    assert entries is not None
    entry = entries[0]
    assert entry is not None
    fields = entry[1]
    assert fields is not None
    assert fields["envelope"] == envelope


async def test_failed_reply_preserves_correlation_for_retry(
    redis: Redis, sessions: SessionManager
) -> None:
    policy = _policy(redis)
    authz = AuthorizationModule(redis, enable_rbac=False)
    await authz.set_permissions("responder", allowed_targets=[])
    service = IngressService(redis=redis, sessions=sessions, policy=policy)
    pending_key = "mas.pending_request:retry"
    value = json.dumps(
        {
            "agent_id": "requester",
            "instance_id": "requester-inst",
            "target_id": "responder",
            "expires_at": time.time() + 10,
        }
    )
    await redis.set(pending_key, value, ex=10)
    reply = {
        "sender_id": "responder",
        "sender_instance_id": "responder-inst",
        "correlation_id": "retry",
        "message_type": "answer",
        "data_json": "{}",
    }
    with pytest.raises(PermissionDeniedError):
        await service.reply_message(**reply)
    assert await redis.get(pending_key) == value
    assert 0 < await redis.pttl(pending_key) <= 10_000
    await authz.set_permissions("responder", allowed_targets=["requester"])
    assert await service.reply_message(**reply)
    assert await redis.exists(pending_key) == 0
    assert await redis.xlen("agent.stream:requester:requester-inst") == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("agent_id", 1), ("instance_id", []), ("expires_at", True), ("expires_at", "NaN")],
)
async def test_reply_rejects_malformed_correlation_record(
    redis: Redis,
    sessions: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    policy = _policy(redis)
    route = AsyncMock()
    monkeypatch.setattr(policy, "ingest_and_route", route)
    service = IngressService(redis=redis, sessions=sessions, policy=policy)
    origin: dict[str, object] = {
        "agent_id": "requester",
        "instance_id": "requester-inst",
        "target_id": "responder",
        "expires_at": time.time() + 10,
    }
    origin[field] = value
    await redis.set("mas.pending_request:malformed", json.dumps(origin), ex=10)
    with pytest.raises(InvalidArgumentError, match="unknown_correlation_id"):
        await service.reply_message(
            sender_id="responder",
            sender_instance_id="responder-inst",
            correlation_id="malformed",
            message_type="answer",
            data_json="{}",
        )
    route.assert_not_awaited()


async def test_request_preserves_millisecond_timeout(
    redis: Redis, sessions: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _policy(redis)
    monkeypatch.setattr(policy, "ingest_and_route", AsyncMock())
    service = IngressService(redis=redis, sessions=sessions, policy=policy)
    _message_id, correlation_id = await service.request_message(
        sender_id="requester",
        sender_instance_id="requester-inst",
        target_id="responder",
        message_type="question",
        data_json="{}",
        timeout_ms=100,
    )
    assert 0 < await redis.pttl(f"mas.pending_request:{correlation_id}") <= 100


async def test_audit_failure_prevents_message_routing(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await redis.hset("agent:worker", mapping={"status": "ACTIVE"})
    await redis.sadd("agent:requester:allowed_targets", "worker")
    policy = _policy(redis)
    audit = AsyncMock(side_effect=RuntimeError("audit unavailable"))
    monkeypatch.setattr(policy._audit, "log_message", audit)
    message = EnvelopeMessage(
        sender_id="requester", target_id="worker", message_type="work", data={}
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await policy.ingest_and_route(message)
    assert await redis.xlen("agent.stream:worker") == 0


async def test_discovery_is_sorted_and_filters_active_capabilities(
    redis: Redis,
) -> None:
    metadata: JsonObject = {"nested": {"key": "value"}}
    registry = RegistryService(
        redis=redis,
        agents={
            "z": AgentDefinition(
                agent_id="z", capabilities=["work"], metadata=metadata
            ),
            "a": AgentDefinition(agent_id="a", capabilities=["work"], metadata={}),
            "b": AgentDefinition(agent_id="b", capabilities=["other"], metadata={}),
        },
    )
    await registry.bootstrap_registry()
    for agent_id in ("z", "a", "b"):
        await registry._leases.acquire(agent_id, "one")
    await redis.sadd("agent:requester:allowed_targets", "*")
    records = await registry.discover(agent_id="requester", capabilities=["work"])
    assert [record["id"] for record in records] == ["a", "z"]
    assert records[1]["metadata"] == metadata


async def test_instance_identifier_rejects_trailing_newline(
    sessions: SessionManager,
) -> None:
    with pytest.raises(InvalidArgumentError, match="invalid_instance_id"):
        await sessions.connect(
            agent_id="worker", instance_id="worker\n", task_factory=_task_factory
        )
    active = await sessions.snapshot()
    assert len(active) == 3
    assert len(await sessions.snapshot()) == 3


@pytest.mark.parametrize("half_close", [True, False])
async def test_transport_releases_session_when_client_input_ends_or_is_invalid(
    mas_server_factory: Callable[
        [dict[str, AgentDefinition] | None], Awaitable[MASServer]
    ],
    test_tls: TlsFixture,
    half_close: bool,
) -> None:
    server = await mas_server_factory(
        {"worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})}
    )
    tls = test_tls.client("worker")
    credentials = grpc.ssl_channel_credentials(
        root_certificates=Path(tls.root_ca_path).read_bytes(),
        private_key=Path(tls.client_key_path).read_bytes(),
        certificate_chain=Path(tls.client_cert_path).read_bytes(),
    )
    async with grpc_aio.secure_channel(server.bound_addr, credentials) as channel:
        stub = mas_pb2_grpc.RuntimeServiceStub(channel)
        call = stub.Transport()
        await call.write(
            mas_pb2.ClientEvent(hello=mas_pb2.Hello(instance_id="worker-inst"))
        )
        welcome = await asyncio.wait_for(call.read(), timeout=2)
        assert welcome.HasField("welcome")
        if half_close:
            await call.done_writing()
            assert await asyncio.wait_for(call.read(), timeout=2) is grpc_aio.EOF
        else:
            await call.write(mas_pb2.ClientEvent())
            with pytest.raises(grpc_aio.AioRpcError) as exc:
                await asyncio.wait_for(call.read(), timeout=2)
            assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert server._sessions is not None
    async with asyncio.timeout(2):
        while await server._sessions.snapshot():
            await asyncio.sleep(0.01)
