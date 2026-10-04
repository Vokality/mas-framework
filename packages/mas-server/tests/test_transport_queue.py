"""Transport queue scheduling and failure precedence without Redis."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field, replace

import grpc
import grpc.aio as grpc_aio
import pytest
import pytest_asyncio
from mas_core.sessions import SessionLease
from mas_gateway.config import GatewaySettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.errors import InvalidArgumentError, RpcError, UnauthenticatedError
from mas_server.runtime import MASServer
from mas_server.servicer import MasGrpcServicer
from mas_server.types import MASServerSettings, OutboundDelivery, Session, TlsConfig
from redis.exceptions import ConnectionError as RedisConnectionError

pytestmark = pytest.mark.asyncio


class TrackedQueue(asyncio.Queue[OutboundDelivery]):
    """Expose asynchronous dequeue work and cancellation at the queue boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0
        self.waiting = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def get(self) -> OutboundDelivery:
        self.get_calls += 1
        self.waiting.set()
        try:
            return await super().get()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@dataclass(slots=True)
class TransportHarness:
    """Typed session and native gRPC adapter with controlled domain boundaries."""

    session: Session
    queue: TrackedQueue
    channel: grpc_aio.Channel
    inbound_started: asyncio.Event = field(default_factory=asyncio.Event)
    inbound_finished: asyncio.Event = field(default_factory=asyncio.Event)
    disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    inbound_error: RpcError | None = None
    auth_calls: int = 0
    auth_failure_at: int | None = None
    lease_expiry_at: int | None = None
    auth_denials: list[str] = field(default_factory=list)

    async def connect(
        self,
    ) -> grpc_aio.StreamStreamCall[mas_pb2.ClientEvent, mas_pb2.ServerEvent]:
        """Open a native stream and read its welcome before returning."""

        def serialize(event: mas_pb2.ClientEvent) -> bytes:
            return event.SerializeToString()

        method = self.channel.stream_stream(
            "/mas.runtime.v1.RuntimeService/Transport",
            request_serializer=serialize,
            response_deserializer=mas_pb2.ServerEvent.FromString,
        )
        call = method(timeout=2)
        await call.write(
            mas_pb2.ClientEvent(hello=mas_pb2.Hello(instance_id="instance"))
        )
        welcome = await call.read()
        assert isinstance(welcome, mas_pb2.ServerEvent)
        assert welcome.HasField("welcome")
        return call


async def _idle() -> None:
    await asyncio.Event().wait()


def _delivery(index: int) -> OutboundDelivery:
    return OutboundDelivery(
        delivery=mas_pb2.Delivery(delivery_id=f"delivery-{index}", envelope_json="{}")
    )


@pytest_asyncio.fixture
async def transport(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[TransportHarness]:
    queue = TrackedQueue()
    session = Session(
        agent_id="worker",
        instance_id="instance",
        outbound=queue,
        inflight={},
        task=asyncio.create_task(_idle()),
        lease=SessionLease("worker", "instance", "owner", time.monotonic() + 60),
    )
    runtime = MASServer(
        settings=MASServerSettings(
            listen_addr="127.0.0.1:0",
            tls=TlsConfig("unused.pem", "unused.key", "unused-ca.pem"),
            agents={},
        ),
        gateway=GatewaySettings(),
    )
    servicer = MasGrpcServicer(runtime)
    server = grpc_aio.server()
    mas_pb2_grpc.add_RuntimeServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc_aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            harness = TransportHarness(session, queue, channel)

            async def connect(*, agent_id: str, instance_id: str) -> Session:
                assert (agent_id, instance_id) == ("worker", "instance")
                return session

            async def disconnect(*, agent_id: str, instance_id: str) -> None:
                assert (agent_id, instance_id) == ("worker", "instance")
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
                harness.disconnected.set()

            async def consume(
                *,
                request_iterator: AsyncIterator[mas_pb2.ClientEvent],
                agent_id: str,
                instance_id: str,
                context: grpc_aio.ServicerContext,
            ) -> None:
                harness.inbound_started.set()
                if harness.inbound_error is not None:
                    raise harness.inbound_error
                await harness.inbound_finished.wait()

            async def identity(
                _context: grpc_aio.ServicerContext, *, tls: TlsConfig | None = None
            ) -> str:
                harness.auth_calls += 1
                if harness.auth_calls == harness.auth_failure_at:
                    raise UnauthenticatedError("certificate_revoked")
                if harness.auth_calls == harness.lease_expiry_at:
                    session.lease = replace(session.lease, expires_at=0)
                await asyncio.sleep(0)
                return "worker"

            async def audit_denial(reason: str) -> None:
                harness.auth_denials.append(reason)

            monkeypatch.setattr(runtime, "connect_session", connect)
            monkeypatch.setattr(runtime, "disconnect_session", disconnect)
            monkeypatch.setattr(runtime, "audit_authentication_denied", audit_denial)
            monkeypatch.setattr(servicer, "_consume_client_events", consume)
            monkeypatch.setattr("mas_server.servicer.spiffe_agent_id", identity)
            yield harness
    finally:
        await server.stop(grace=0)
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)


@pytest.mark.parametrize("worker_done", [False, True])
async def test_ready_inbound_failure_beats_queued_delivery_and_stopped_worker(
    transport: TransportHarness, worker_done: bool
) -> None:
    transport.inbound_error = InvalidArgumentError("invalid_inbound")
    transport.queue.put_nowait(_delivery(0))
    if worker_done:
        transport.session.task.cancel()
        await asyncio.gather(transport.session.task, return_exceptions=True)
    call = await transport.connect()
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call.read()
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc.value.details() == "invalid_inbound"
    assert transport.queue.qsize() == 1
    assert transport.queue.get_calls == 0
    assert transport.auth_calls == 1


@pytest.mark.parametrize("storage_failure", [False, True])
async def test_ready_worker_stops_before_queued_delivery(
    transport: TransportHarness, storage_failure: bool
) -> None:
    transport.session.task.cancel()
    await asyncio.gather(transport.session.task, return_exceptions=True)

    async def stopped() -> None:
        if storage_failure:
            raise RedisConnectionError("private-storage-detail")

    transport.session.task = asyncio.create_task(stopped())
    await asyncio.gather(transport.session.task, return_exceptions=True)
    transport.queue.put_nowait(_delivery(0))
    call = await transport.connect()
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call.read()
    assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
    assert exc.value.details() == (
        "storage_unavailable" if storage_failure else "delivery_worker_stopped"
    )
    assert transport.queue.qsize() == 1
    assert transport.queue.get_calls == 0
    assert transport.auth_calls == 1


async def test_ready_queue_avoids_one_async_dequeue_per_delivery(
    transport: TransportHarness,
) -> None:
    for index in range(16):
        transport.queue.put_nowait(_delivery(index))
    call = await transport.connect()
    for index in range(16):
        event = await call.read()
        assert isinstance(event, mas_pb2.ServerEvent)
        assert event.delivery.delivery_id == f"delivery-{index}"
    assert transport.queue.get_calls <= 1
    assert transport.auth_calls == 17
    assert transport.session.capacity_changed.is_set()
    transport.inbound_finished.set()
    assert await call.read() == grpc_aio.EOF
    await asyncio.wait_for(transport.disconnected.wait(), timeout=2)


async def test_empty_queue_waits_for_delivery_and_cancels_on_disconnect(
    transport: TransportHarness,
) -> None:
    call = await transport.connect()
    await asyncio.wait_for(transport.queue.waiting.wait(), timeout=2)
    assert transport.queue.get_calls == 1
    transport.queue.put_nowait(_delivery(0))
    event = await call.read()
    assert isinstance(event, mas_pb2.ServerEvent)
    assert event.delivery.delivery_id == "delivery-0"
    assert transport.session.capacity_changed.is_set()
    transport.queue.waiting.clear()
    async with asyncio.timeout(2):
        while transport.queue.get_calls != 2:
            await asyncio.sleep(0)
    assert call.cancel()
    await asyncio.wait_for(transport.disconnected.wait(), timeout=2)
    assert transport.queue.cancelled.is_set()
    assert transport.session.task.done()


async def test_inbound_close_cancels_empty_queue_wait(
    transport: TransportHarness,
) -> None:
    call = await transport.connect()
    await asyncio.wait_for(transport.queue.waiting.wait(), timeout=2)
    transport.inbound_finished.set()
    assert await call.read() == grpc_aio.EOF
    await asyncio.wait_for(transport.disconnected.wait(), timeout=2)
    assert transport.queue.cancelled.is_set()
    assert transport.session.task.done()


@pytest.mark.parametrize("revoked", [False, True])
async def test_ready_queue_rechecks_authentication_and_lease_before_each_write(
    transport: TransportHarness, revoked: bool
) -> None:
    transport.queue.put_nowait(_delivery(0))
    transport.queue.put_nowait(_delivery(1))
    if revoked:
        transport.auth_failure_at = 3
    else:
        transport.lease_expiry_at = 3
    call = await transport.connect()
    event = await call.read()
    assert isinstance(event, mas_pb2.ServerEvent)
    assert event.delivery.delivery_id == "delivery-0"
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call.read()
    assert exc.value.code() == (
        grpc.StatusCode.UNAUTHENTICATED if revoked else grpc.StatusCode.UNAVAILABLE
    )
    assert exc.value.details() == (
        "certificate_revoked" if revoked else "session_lease_lost"
    )
    assert transport.auth_calls == 3
    assert transport.auth_denials == (["certificate_revoked"] if revoked else [])
    assert transport.queue.get_calls == 0
