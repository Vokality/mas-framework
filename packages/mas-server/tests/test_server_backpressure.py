from __future__ import annotations

import asyncio
from typing import cast

from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_server.delivery import DeliveryService
from mas_server.routing import MessageRouter
from mas_server.sessions import SessionManager
from mas_server.types import (
    AgentDefinition,
    InflightDelivery,
    MASServerSettings,
    TlsConfig,
)
from redis.asyncio import Redis


def _event(delivery_id: str) -> mas_pb2.ServerEvent:
    return mas_pb2.ServerEvent(
        delivery=mas_pb2.Delivery(delivery_id=delivery_id, envelope_json="{}")
    )


async def _idle_task() -> None:
    await asyncio.Event().wait()


def _task_factory(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[mas_pb2.ServerEvent],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_task())


def test_drop_oldest_outbound_removes_inflight() -> None:
    outbound: asyncio.Queue[mas_pb2.ServerEvent] = asyncio.Queue(maxsize=2)
    inflight: dict[str, InflightDelivery] = {
        "d1": InflightDelivery(
            stream_name="agent.stream:worker",
            group="agents",
            entry_id="1-0",
            envelope_json="{}",
            received_at=0.0,
        ),
        "d2": InflightDelivery(
            stream_name="agent.stream:worker",
            group="agents",
            entry_id="2-0",
            envelope_json="{}",
            received_at=0.0,
        ),
    }

    outbound.put_nowait(_event("d1"))
    outbound.put_nowait(_event("d2"))

    dropped = SessionManager.drop_oldest_outbound(outbound, inflight)

    assert dropped == 1
    assert "d1" not in inflight
    assert outbound.qsize() == 1


class _FakeRedis:
    def __init__(self) -> None:
        self.service: DeliveryService | None = None
        self.counts: list[int] = []

    async def xgroup_create(
        self, _stream_name: str, _group: str, *, id: str, mkstream: bool
    ) -> None:
        del id, mkstream

    async def xreadgroup(
        self,
        _group: str,
        _consumer: str,
        *,
        streams: dict[str, str],
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del streams, block
        self.counts.append(count)
        if self.service is None:
            raise AssertionError("service not attached")
        self.service.set_running(False)
        return [
            (
                "agent.stream:worker",
                [(f"{index}-0", {"envelope": "{}"}) for index in range(1, 4)],
            )
        ]


class _FailingRequeueRedis:
    def __init__(self) -> None:
        self.xadd_calls = 0
        self.xack_calls = 0

    async def xadd(self, _stream_name: str, _fields: dict[str, str]) -> None:
        self.xadd_calls += 1
        raise RuntimeError("requeue failed")

    async def xack(self, _stream_name: str, _group: str, _entry_id: str) -> None:
        self.xack_calls += 1


class _AckOnlyRedis:
    def __init__(self) -> None:
        self.xack_calls = 0

    async def xack(self, _stream_name: str, _group: str, _entry_id: str) -> None:
        self.xack_calls += 1


class _DlqRedis:
    def __init__(self) -> None:
        self.fields: list[dict[str, str]] = []

    async def xadd(self, _stream_name: str, fields: dict[str, str]) -> str:
        self.fields.append(fields)
        return "1-0"


class _FailingDlqRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def write_dlq(self, *, envelope_json: str, reason: str) -> bool:
        del envelope_json, reason
        self.calls += 1
        return False


async def test_stream_loop_honors_max_in_flight_within_batch() -> None:
    redis = _FakeRedis()
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path="server.pem",
            server_key_path="server.key",
            client_ca_path="ca.pem",
        ),
        agents={},
        max_in_flight=1,
    )
    service = DeliveryService(
        redis=cast("Redis[str]", redis),
        settings=settings,
        sessions=SessionManager(agents={}),
        router=cast(MessageRouter, object()),
        circuit_breaker=None,
    )
    redis.service = service
    outbound: asyncio.Queue[mas_pb2.ServerEvent] = asyncio.Queue(maxsize=10)
    inflight: dict[str, InflightDelivery] = {}

    service.set_running(True)
    await service._stream_loop(
        agent_id="worker",
        instance_id="worker-inst",
        outbound=outbound,
        inflight=inflight,
    )

    assert redis.counts == [1]
    assert outbound.qsize() == 1
    assert len(inflight) == 1


async def test_retryable_nack_does_not_ack_when_requeue_fails() -> None:
    sessions = SessionManager(
        agents={
            "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})
        }
    )
    session = await sessions.connect(
        agent_id="worker",
        instance_id="worker-inst",
        task_factory=_task_factory,
    )
    session.inflight["delivery-1"] = InflightDelivery(
        stream_name="agent.stream:worker",
        group="agents",
        entry_id="1-0",
        envelope_json="{}",
        received_at=0.0,
    )
    redis = _FailingRequeueRedis()
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path="server.pem",
            server_key_path="server.key",
            client_ca_path="ca.pem",
        ),
        agents={},
    )
    service = DeliveryService(
        redis=cast("Redis[str]", redis),
        settings=settings,
        sessions=sessions,
        router=cast(MessageRouter, object()),
        circuit_breaker=None,
    )

    try:
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="delivery-1",
            reason="retry",
            retryable=True,
        )

        assert redis.xadd_calls == 1
        assert redis.xack_calls == 0
    finally:
        active = await sessions.snapshot_and_clear()
        for active_session in active:
            active_session.task.cancel()
        await asyncio.gather(
            *(active_session.task for active_session in active),
            return_exceptions=True,
        )


async def test_nonretryable_nack_does_not_ack_when_dlq_write_fails() -> None:
    sessions = SessionManager(
        agents={
            "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})
        }
    )
    session = await sessions.connect(
        agent_id="worker",
        instance_id="worker-inst",
        task_factory=_task_factory,
    )
    session.inflight["delivery-1"] = InflightDelivery(
        stream_name="agent.stream:worker",
        group="agents",
        entry_id="1-0",
        envelope_json="{}",
        received_at=0.0,
    )
    redis = _FailingRequeueRedis()
    router = _FailingDlqRouter()
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path="server.pem",
            server_key_path="server.key",
            client_ca_path="ca.pem",
        ),
        agents={},
    )
    service = DeliveryService(
        redis=cast("Redis[str]", redis),
        settings=settings,
        sessions=sessions,
        router=cast(MessageRouter, router),
        circuit_breaker=None,
    )

    try:
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="delivery-1",
            reason="handler_error",
            retryable=False,
        )

        assert router.calls == 1
        assert redis.xack_calls == 0
    finally:
        active = await sessions.snapshot_and_clear()
        for active_session in active:
            active_session.task.cancel()
        await asyncio.gather(
            *(active_session.task for active_session in active),
            return_exceptions=True,
        )


async def test_nonretryable_nack_acks_when_dlq_disabled() -> None:
    sessions = SessionManager(
        agents={
            "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})
        }
    )
    session = await sessions.connect(
        agent_id="worker",
        instance_id="worker-inst",
        task_factory=_task_factory,
    )
    session.inflight["delivery-1"] = InflightDelivery(
        stream_name="agent.stream:worker",
        group="agents",
        entry_id="1-0",
        envelope_json="{}",
        received_at=0.0,
    )
    redis = _AckOnlyRedis()
    router = MessageRouter(redis=cast("Redis[str]", redis), dlq_enabled=False)
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig(
            server_cert_path="server.pem",
            server_key_path="server.key",
            client_ca_path="ca.pem",
        ),
        agents={},
    )
    service = DeliveryService(
        redis=cast("Redis[str]", redis),
        settings=settings,
        sessions=sessions,
        router=router,
        circuit_breaker=None,
    )

    try:
        await service.handle_nack(
            agent_id="worker",
            instance_id="worker-inst",
            delivery_id="delivery-1",
            reason="handler_error",
            retryable=False,
        )

        assert redis.xack_calls == 1
    finally:
        active = await sessions.snapshot_and_clear()
        for active_session in active:
            active_session.task.cancel()
        await asyncio.gather(
            *(active_session.task for active_session in active),
            return_exceptions=True,
        )


async def test_dlq_write_falls_back_for_invalid_envelope() -> None:
    redis = _DlqRedis()
    router = MessageRouter(redis=cast("Redis[str]", redis), dlq_enabled=True)

    written = await router.write_dlq(envelope_json="{", reason="invalid_envelope")

    assert written is True
    assert redis.fields[0]["message_id"] == ""
    assert redis.fields[0]["decision"] == "DLQ"
    assert redis.fields[0]["reason"] == "invalid_envelope"


async def test_write_dlq_returns_true_when_disabled() -> None:
    redis = _DlqRedis()
    router = MessageRouter(redis=cast("Redis[str]", redis), dlq_enabled=False)

    written = await router.write_dlq(envelope_json="{}", reason="disabled")

    assert written is True
    assert redis.fields == []
