"""Capacity releases wake fenced delivery readers without a polling timer."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Literal

import grpc
import pytest
import pytest_asyncio
from mas_core.sessions import SessionLease
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_server.delivery import _READ_SCRIPT, DeliveryService
from mas_server.errors import RpcError
from mas_server.routing import MessageRouter
from mas_server.sessions import SessionManager
from mas_server.types import (
    InflightDelivery,
    MASServerSettings,
    OutboundDelivery,
    Session,
    TlsConfig,
)
from redis.asyncio import Redis

pytestmark = pytest.mark.asyncio


class ObservedEvent(asyncio.Event):
    """Expose actual waiting and allow a release immediately before suspension."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: asyncio.Queue[None] = asyncio.Queue()
        self.before_wait: Callable[[], Awaitable[None]] | None = None

    async def wait(self) -> Literal[True]:
        self.waits.put_nowait(None)
        if self.before_wait is not None:
            release, self.before_wait = self.before_wait, None
            await release()
        return await super().wait()


@dataclass(slots=True)
class CapacityEnvironment:
    """A real delivery loop with local session state and an isolated Redis boundary."""

    service: DeliveryService
    sessions: SessionManager
    session: Session
    notification: ObservedEvent
    worker: asyncio.Task[None] | None = None
    reads: list[int] = field(default_factory=list)
    read: asyncio.Event = field(default_factory=asyncio.Event)

    def start(self) -> asyncio.Task[None]:
        """Run the actual reader against its matching local session state."""
        self.service.set_running(True)
        self.worker = self.service.start_stream_task(
            "worker", "instance", self.session.outbound, self.session.inflight
        )
        return self.worker


async def idle() -> None:
    await asyncio.Event().wait()


def inflight(index: int) -> InflightDelivery:
    return InflightDelivery("agent.stream:worker", "agents", f"{index}-0", "{}", 0)


@pytest_asyncio.fixture
async def capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[CapacityEnvironment]:
    redis = Redis.from_url("redis://unused.invalid", decode_responses=True)
    sessions = SessionManager(agents={}, redis=redis)
    notification = ObservedEvent()
    session = Session(
        agent_id="worker",
        instance_id="instance",
        outbound=asyncio.Queue(maxsize=1),
        inflight={},
        task=asyncio.create_task(idle()),
        lease=SessionLease("worker", "instance", "owner", time.monotonic() + 60),
        capacity_changed=notification,
    )
    sessions._sessions[("worker", "instance")] = session
    service = DeliveryService(
        redis=redis,
        settings=MASServerSettings(
            listen_addr="unused",
            tls=TlsConfig("unused", "unused", "unused"),
            agents={},
            max_in_flight=2,
        ),
        sessions=sessions,
        router=MessageRouter(redis=redis, dlq_enabled=False),
        circuit_breaker=None,
    )
    environment = CapacityEnvironment(service, sessions, session, notification)

    async def ensure_group(*, stream_name: str, group: str) -> None:
        assert stream_name in {"agent.stream:worker", "agent.stream:worker:instance"}
        assert group == "agents"

    async def reclaim(
        stream_name: str,
        group: str,
        consumer: str,
        start_id: str,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[OutboundDelivery],
        inflight: dict[str, InflightDelivery],
    ) -> str:
        return start_id

    async def read(script: str, numkeys: int, *values: str | int) -> object:
        assert script == _READ_SCRIPT
        assert values[numkeys - 1] == "mas.session:worker:instance"
        assert values[numkeys] == "owner"
        count = values[-1]
        assert isinstance(count, int)
        environment.reads.append(count * (numkeys - 1))
        environment.read.set()
        service.set_running(False)
        return [1, [["agent.stream:worker", [["100-0", ["envelope", "{}"]]]]]]

    async def polling_sleep(delay: float) -> None:
        pytest.fail(f"Capacity release must not wait for a {delay}s polling timer")

    monkeypatch.setattr(service, "_ensure_group_exists", ensure_group)
    monkeypatch.setattr(service, "_reclaim_pending", reclaim)
    monkeypatch.setattr(redis, "eval", read)
    monkeypatch.setattr("mas_server.delivery.asyncio.sleep", polling_sleep)
    try:
        yield environment
    finally:
        for task in (environment.worker, session.task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await redis.aclose()


@pytest.mark.parametrize("outbound_full", [False, True])
async def test_capacity_release_wakes_reader_without_polling_and_preserves_cap(
    capacity: CapacityEnvironment, outbound_full: bool
) -> None:
    capacity.session.inflight["first"] = inflight(1)
    if outbound_full:
        capacity.session.outbound.put_nowait(OutboundDelivery(mas_pb2.Delivery()))
    else:
        capacity.session.inflight["second"] = inflight(2)
    worker = capacity.start()
    await asyncio.wait_for(capacity.notification.waits.get(), timeout=1)
    assert capacity.reads == []
    if outbound_full:
        capacity.session.outbound.get_nowait()
        capacity.session.capacity_changed.set()
    else:
        assert (
            await capacity.sessions.pop_inflight(
                agent_id="worker", instance_id="instance", delivery_id="second"
            )
            is not None
        )
    await asyncio.wait_for(worker, timeout=1)
    assert capacity.reads == [1]
    assert len(capacity.session.inflight) == 2
    assert capacity.session.outbound.qsize() == 1


async def test_release_between_capacity_check_and_wait_is_not_lost(
    capacity: CapacityEnvironment,
) -> None:
    capacity.session.inflight.update(first=inflight(1), second=inflight(2))

    async def release() -> None:
        assert (
            await capacity.sessions.pop_inflight(
                agent_id="worker", instance_id="instance", delivery_id="first"
            )
            is not None
        )

    capacity.notification.before_wait = release
    await asyncio.wait_for(capacity.start(), timeout=1)
    assert capacity.reads == [1]
    assert len(capacity.session.inflight) == 2


async def test_spurious_capacity_notification_does_not_overfill(
    capacity: CapacityEnvironment,
) -> None:
    capacity.session.inflight.update(first=inflight(1), second=inflight(2))
    worker = capacity.start()
    await asyncio.wait_for(capacity.notification.waits.get(), timeout=1)
    capacity.notification.set()
    await asyncio.wait_for(capacity.notification.waits.get(), timeout=1)
    assert capacity.reads == []
    assert len(capacity.session.inflight) == 2
    worker.cancel()
    await asyncio.wait_for(worker, timeout=1)
    assert capacity.reads == []


async def test_exhausted_wait_observes_lease_expiry_without_notification(
    capacity: CapacityEnvironment,
) -> None:
    capacity.session.inflight.update(first=inflight(1), second=inflight(2))
    worker = capacity.start()
    await asyncio.wait_for(capacity.notification.waits.get(), timeout=1)
    capacity.session.lease = replace(capacity.session.lease, expires_at=0)
    with pytest.raises(RpcError) as error:
        await asyncio.wait_for(worker, timeout=0.8)
    assert error.value.status == grpc.StatusCode.UNAVAILABLE
    assert error.value.message == "session_lease_lost"
    assert capacity.reads == []


async def test_unknown_ack_does_not_signal_a_capacity_release(
    capacity: CapacityEnvironment,
) -> None:
    assert (
        await capacity.sessions.pop_inflight(
            agent_id="worker", instance_id="instance", delivery_id="missing"
        )
        is None
    )
    assert not capacity.notification.is_set()
