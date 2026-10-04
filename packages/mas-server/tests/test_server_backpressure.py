"""Real storage regressions for backpressure and delivery failure safety."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_server.delivery import _READ_SCRIPT, DeliveryService
from mas_server.routing import MessageRouter
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
from redis.exceptions import ResponseError


async def _idle_task() -> None:
    await asyncio.Event().wait()


def _task_factory(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[OutboundDelivery],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_task())


def test_drop_oldest_outbound_removes_inflight() -> None:
    outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue(maxsize=2)
    inflight = {
        identity: InflightDelivery(
            "agent.stream:worker", "agents", f"{index}-0", "{}", 0
        )
        for index, identity in enumerate(("d1", "d2"), 1)
    }
    for identity in inflight:
        outbound.put_nowait(
            OutboundDelivery(delivery=mas_pb2.Delivery(delivery_id=identity))
        )
    assert SessionManager.drop_oldest_outbound(outbound, inflight) == 1
    assert "d1" not in inflight
    assert outbound.qsize() == 1


@dataclass(slots=True)
class DeliveryEnvironment:
    """Real pending ownership shared by delivery regressions."""

    session: Session
    service: DeliveryService
    router: MessageRouter


@pytest_asyncio.fixture
async def delivery_environment(redis: Redis) -> AsyncGenerator[DeliveryEnvironment]:
    agents = {"worker": AgentDefinition("worker", [], {})}
    sessions = SessionManager(agents=agents, redis=redis)
    session = await sessions.connect(
        agent_id="worker", instance_id="worker-inst", task_factory=_task_factory
    )
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig("server.pem", "server.key", "ca.pem"),
        agents=agents,
        max_in_flight=1,
    )
    router = MessageRouter(redis=redis, dlq_enabled=True)
    service = DeliveryService(
        redis=redis,
        settings=settings,
        sessions=sessions,
        router=router,
        circuit_breaker=None,
    )
    try:
        yield DeliveryEnvironment(session, service, router)
    finally:
        await sessions.disconnect(agent_id="worker", instance_id="worker-inst")


async def _make_pending(redis: Redis, environment: DeliveryEnvironment) -> None:
    stream = "agent.stream:worker"
    await redis.xgroup_create(stream, "agents", id="0-0", mkstream=True)
    entry = TypeAdapter(str).validate_python(
        await redis.xadd(stream, {"envelope": "{}"})
    )
    consumer = f"worker-worker-inst-{environment.session.lease.owner}"
    await redis.xreadgroup("agents", consumer, streams={stream: ">"}, count=1)
    environment.session.inflight["delivery-1"] = InflightDelivery(
        stream, "agents", entry, "{}", 0, consumer=consumer
    )


async def test_stream_loop_honors_max_in_flight_within_batch(
    redis: Redis,
    delivery_environment: DeliveryEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = delivery_environment
    counts: list[int] = []
    original = redis.eval

    async def read(script: str, numkeys: int, *values: str | int) -> object:
        if script != _READ_SCRIPT:
            return await original(script, numkeys, *values)
        counts.append(TypeAdapter(int).validate_python(values[-1]))
        environment.service.set_running(False)
        return [
            1,
            [
                (
                    "agent.stream:worker",
                    [(f"{index}-0", ["envelope", "{}"]) for index in range(1, 4)],
                )
            ],
        ]

    monkeypatch.setattr(redis, "eval", read)
    environment.service.set_running(True)
    outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue(maxsize=10)
    inflight: dict[str, InflightDelivery] = {}
    await environment.service._stream_loop(
        agent_id="worker",
        instance_id="worker-inst",
        outbound=outbound,
        inflight=inflight,
    )
    assert counts == [1]
    assert outbound.qsize() == len(inflight) == 1


async def test_retryable_nack_does_not_ack_when_requeue_fails(
    redis: Redis,
    delivery_environment: DeliveryEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = delivery_environment
    await _make_pending(redis, environment)

    async def unavailable(script: str, numkeys: int, *values: str | int) -> int:
        raise RedisConnectionError("unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(redis, "eval", unavailable)
        with pytest.raises(RedisConnectionError):
            await environment.service.handle_nack(
                agent_id="worker",
                instance_id="worker-inst",
                delivery_id="delivery-1",
                reason="retry",
                retryable=True,
            )
    assert (
        len(await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10))
        == 1
    )
    assert await redis.xlen("agent.stream:worker") == 1


async def test_nonretryable_nack_does_not_ack_when_dlq_write_fails(
    redis: Redis, delivery_environment: DeliveryEnvironment
) -> None:
    environment = delivery_environment
    await _make_pending(redis, environment)
    await redis.set("dlq:messages", "wrong-type")
    with pytest.raises(ResponseError, match="invalid_dlq_stream_type"):
        await environment.service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="delivery-1",
            reason="handler_error",
            retryable=False,
        )
    assert (
        len(await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10))
        == 1
    )
    assert await redis.xlen("agent.stream:worker") == 1


async def test_nonretryable_nack_acks_when_dlq_disabled(
    redis: Redis, delivery_environment: DeliveryEnvironment
) -> None:
    environment = delivery_environment
    await _make_pending(redis, environment)
    environment.service._router = MessageRouter(redis=redis, dlq_enabled=False)
    await environment.service.handle_nack(
        agent_id="worker",
        instance_id="worker-inst",
        delivery_id="delivery-1",
        reason="handler_error",
        retryable=False,
    )
    assert await redis.xlen("agent.stream:worker") == 0
    assert not await redis.xpending_range("agent.stream:worker", "agents", "-", "+", 10)
    assert not await redis.exists("dlq:messages")


async def test_dlq_write_falls_back_for_invalid_envelope(redis: Redis) -> None:
    router = MessageRouter(redis=redis, dlq_enabled=True)
    assert await router.write_dlq(envelope_json="{", reason="invalid_envelope")
    entries = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
        await redis.xrange("dlq:messages")
    )
    assert entries[0][1]["message_id"] == ""
    assert entries[0][1]["decision"] == "DLQ"
    assert entries[0][1]["reason"] == "invalid_envelope"


async def test_write_dlq_returns_true_when_disabled(redis: Redis) -> None:
    router = MessageRouter(redis=redis, dlq_enabled=False)
    assert await router.write_dlq(envelope_json="{}", reason="disabled")
    assert not await redis.exists("dlq:messages")
