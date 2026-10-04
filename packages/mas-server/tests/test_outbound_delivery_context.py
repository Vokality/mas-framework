"""Validated delivery metadata survives the local queue and native gRPC boundary."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Never

import grpc.aio as grpc_aio
import pytest
import pytest_asyncio
from mas_core.protocol import EnvelopeMessage, MessageMeta
from mas_core.sessions import SessionLease
from mas_core.telemetry.runtime import TelemetryRuntime
from mas_gateway.config import GatewaySettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.delivery import _CLAIM_SCRIPT, _READ_SCRIPT, DeliveryService
from mas_server.routing import MessageRouter
from mas_server.runtime import MASServer
from mas_server.servicer import MasGrpcServicer
from mas_server.sessions import SessionManager
from mas_server.types import InflightDelivery, MASServerSettings, Session, TlsConfig
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.asyncio import Redis

pytestmark = pytest.mark.asyncio


async def _idle() -> None:
    await asyncio.Event().wait()


@dataclass(slots=True)
class DeliveryHarness:
    """Real local delivery and gRPC transport with isolated storage and identity."""

    service: DeliveryService
    session: Session
    channel: grpc_aio.Channel
    exporter: InMemorySpanExporter
    inbound_finished: asyncio.Event

    async def read_delivery(self) -> mas_pb2.Delivery:
        """Read the unchanged wire event and finish the transport normally."""
        stub = mas_pb2_grpc.RuntimeServiceStub(self.channel)
        call = stub.Transport(timeout=2)
        await call.write(
            mas_pb2.ClientEvent(hello=mas_pb2.Hello(instance_id="instance"))
        )
        welcome = await call.read()
        assert isinstance(welcome, mas_pb2.ServerEvent) and welcome.HasField("welcome")
        event = await call.read()
        assert isinstance(event, mas_pb2.ServerEvent) and event.HasField("delivery")
        self.inbound_finished.set()
        assert await call.read() == grpc_aio.EOF
        return event.delivery


@pytest_asyncio.fixture
async def delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[DeliveryHarness]:
    redis = Redis.from_url("redis://unused.invalid", decode_responses=True)
    settings = MASServerSettings(
        listen_addr="127.0.0.1:0",
        tls=TlsConfig("unused", "unused", "unused"),
        agents={},
        max_in_flight=3,
    )
    sessions = SessionManager(agents={}, redis=redis)
    session = Session(
        agent_id="worker",
        instance_id="instance",
        outbound=asyncio.Queue(maxsize=3),
        inflight={},
        task=asyncio.create_task(_idle()),
        lease=SessionLease("worker", "instance", "owner", time.monotonic() + 60),
    )
    sessions._sessions[("worker", "instance")] = session
    service = DeliveryService(
        redis=redis,
        settings=settings,
        sessions=sessions,
        router=MessageRouter(redis=redis, dlq_enabled=False),
        circuit_breaker=None,
    )
    runtime = MASServer(settings=settings, gateway=GatewaySettings())
    servicer = MasGrpcServicer(runtime)
    inbound_finished = asyncio.Event()

    async def connect(*, agent_id: str, instance_id: str) -> Session:
        assert (agent_id, instance_id) == ("worker", "instance")
        return session

    async def disconnect(*, agent_id: str, instance_id: str) -> None:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)

    async def consume(
        *,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        agent_id: str,
        instance_id: str,
        context: grpc_aio.ServicerContext,
    ) -> None:
        await inbound_finished.wait()

    async def identity(
        context: grpc_aio.ServicerContext, *, tls: TlsConfig | None = None
    ) -> str:
        return "worker"

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
    )
    monkeypatch.setattr(runtime, "connect_session", connect)
    monkeypatch.setattr(runtime, "disconnect_session", disconnect)
    monkeypatch.setattr(servicer, "_consume_client_events", consume)
    monkeypatch.setattr("mas_server.servicer.spiffe_agent_id", identity)
    monkeypatch.setattr("mas_server.servicer.get_telemetry", lambda: telemetry)
    monkeypatch.setattr("mas_server.delivery.get_telemetry", lambda: telemetry)
    server = grpc_aio.server()
    mas_pb2_grpc.add_RuntimeServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc_aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield DeliveryHarness(service, session, channel, exporter, inbound_finished)
    finally:
        await server.stop(grace=0)
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        await telemetry.shutdown()
        await redis.aclose()


async def test_envelope_is_validated_once_and_parent_survives_native_transport(
    delivery: DeliveryHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = EnvelopeMessage(
        sender_id="sender",
        target_id="worker",
        message_type="example",
        data={"nested": {"value": 42}},
        message_id="message-id",
        meta=MessageMeta(traceparent=f"00-{1:032x}-{2:016x}-01"),
    ).model_dump_json()
    original_validate = EnvelopeMessage.model_validate_json
    validations = 0

    def validate_once(
        cls: type[EnvelopeMessage], value: str | bytes | bytearray
    ) -> EnvelopeMessage:
        nonlocal validations
        assert cls is EnvelopeMessage
        validations += 1
        assert validations == 1, "Transport must reuse validated delivery metadata"
        return original_validate(value)

    monkeypatch.setattr(
        EnvelopeMessage, "model_validate_json", classmethod(validate_once)
    )
    await delivery.service._deliver_entry(
        agent_id="worker",
        instance_id="instance",
        outbound=delivery.session.outbound,
        inflight=delivery.session.inflight,
        stream_name="agent.stream:worker",
        group="agents",
        entry_id="100-0",
        envelope_json=envelope,
    )
    queued = delivery.session.outbound.get_nowait()
    delivery.session.outbound.put_nowait(queued)
    event = await delivery.read_delivery()
    assert event == queued.delivery and event.envelope_json == envelope
    assert validations == 1
    spans = {
        span.name: span
        for span in delivery.exporter.get_finished_spans()
        if span.name
        in {"mas.server.delivery.deliver_entry", "mas.server.transport.write"}
    }
    assert len(spans) == 2
    for span in spans.values():
        assert span.context is not None and span.context.trace_id == 1
        assert span.parent is not None and span.parent.span_id == 2
        assert span.attributes is not None
        assert span.attributes["mas.message_id"] == "message-id"
        assert span.attributes["mas.delivery_id"] == event.delivery_id


@pytest.mark.parametrize("envelope", ["not-json", "{}"])
async def test_invalid_envelope_retains_wire_data_without_invented_metadata(
    delivery: DeliveryHarness, envelope: str
) -> None:
    await delivery.service._deliver_entry(
        agent_id="worker",
        instance_id="instance",
        outbound=delivery.session.outbound,
        inflight=delivery.session.inflight,
        stream_name="agent.stream:worker",
        group="agents",
        entry_id="100-0",
        envelope_json=envelope,
    )
    queued = delivery.session.outbound.get_nowait()
    delivery.session.outbound.put_nowait(queued)
    assert queued.message_id is None and queued.parent is None
    event = await delivery.read_delivery()
    assert event == queued.delivery and event.envelope_json == envelope
    span = next(
        span
        for span in delivery.exporter.get_finished_spans()
        if span.name == "mas.server.transport.write"
    )
    assert span.attributes is not None and "mas.message_id" not in span.attributes


class NoScanInflight(dict[str, InflightDelivery]):
    """Fail if a fresh read walks unrelated in-flight entries."""

    def values(self) -> Never:
        pytest.fail("Fresh '>' reads must not scan local in-flight deliveries")


async def test_fresh_fenced_read_does_not_scan_existing_inflight(
    delivery: DeliveryHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery.session.inflight = NoScanInflight(
        existing=InflightDelivery("agent.stream:worker", "agents", "99-0", "{}", 0)
    )

    async def ensure_group(*, stream_name: str, group: str) -> None:
        return

    async def read(script: str, numkeys: int, *values: str | int) -> object:
        assert values[numkeys - 1] == "mas.session:worker:instance"
        assert values[numkeys] == "owner"
        if script == _CLAIM_SCRIPT:
            return ["0-0", [], []]
        assert script == _READ_SCRIPT
        delivery.service.set_running(False)
        return [1, [["agent.stream:worker", [["100-0", ["envelope", "{}"]]]]]]

    monkeypatch.setattr(delivery.service, "_ensure_group_exists", ensure_group)
    monkeypatch.setattr(delivery.service._redis, "eval", read)
    delivery.service.set_running(True)
    await delivery.service._stream_loop(
        agent_id="worker",
        instance_id="instance",
        outbound=delivery.session.outbound,
        inflight=delivery.session.inflight,
    )
    assert len(delivery.session.inflight) == 2
    assert delivery.session.outbound.qsize() == 1


async def test_reclaim_suppresses_current_inflight_but_delivers_other_entry(
    delivery: DeliveryHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery.session.inflight["existing"] = InflightDelivery(
        "agent.stream:worker", "agents", "99-0", "{}", 0
    )

    async def claim(script: str, numkeys: int, *values: str | int) -> object:
        assert script == _CLAIM_SCRIPT
        assert values[numkeys] == "owner"
        return [
            "101-0",
            [["99-0", ["envelope", "{}"]], ["100-0", ["envelope", "{}"]]],
            [],
        ]

    monkeypatch.setattr(delivery.service._redis, "eval", claim)
    next_id = await delivery.service._reclaim_pending(
        "agent.stream:worker",
        "agents",
        "worker-instance-owner",
        "0-0",
        agent_id="worker",
        instance_id="instance",
        outbound=delivery.session.outbound,
        inflight=delivery.session.inflight,
    )
    assert next_id == "101-0"
    assert len(delivery.session.inflight) == 2
    assert delivery.session.inflight["existing"].entry_id == "99-0"
    assert delivery.session.outbound.qsize() == 1
    event = delivery.session.outbound.get_nowait()
    assert delivery.session.inflight[event.delivery.delivery_id].entry_id == "100-0"
