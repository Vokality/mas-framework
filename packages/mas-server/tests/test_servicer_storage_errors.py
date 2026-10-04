"""Wire-level regressions for unary RPC storage failures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import grpc
import grpc.aio as grpc_aio
import pytest
import pytest_asyncio
from mas_gateway.config import GatewaySettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from mas_server.errors import InvalidArgumentError
from mas_server.registry import RegistryService
from mas_server.runtime import MASServer
from mas_server.servicer import MasGrpcServicer
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
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

pytestmark = pytest.mark.asyncio

_UNARY_METHODS = [
    ("Send", "send_message"),
    ("Request", "request_message"),
    ("Reply", "reply_message"),
    ("Discover", "discover"),
    ("GetState", "get_state_snapshot"),
    ("UpdateState", "update_state"),
    ("ResetState", "reset_state"),
]


async def _authenticated_agent(
    _context: grpc_aio.ServicerContext, *, tls: TlsConfig | None = None
) -> str:
    return "worker"


@pytest_asyncio.fixture
async def unary_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[grpc_aio.Channel, MASServer]]:
    runtime = MASServer(
        settings=MASServerSettings(
            listen_addr="127.0.0.1:0",
            tls=TlsConfig(
                server_cert_path="unused.pem",
                server_key_path="unused.key",
                client_ca_path="unused-ca.pem",
            ),
            agents={},
        ),
        gateway=GatewaySettings(),
    )
    monkeypatch.setattr("mas_server.servicer.spiffe_agent_id", _authenticated_agent)
    server = grpc_aio.server()
    mas_pb2_grpc.add_RuntimeServiceServicer_to_server(MasGrpcServicer(runtime), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc_aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield channel, runtime
    finally:
        await server.stop(grace=0)


@pytest.mark.parametrize(("rpc_method", "runtime_method"), _UNARY_METHODS)
@pytest.mark.parametrize(
    "error_type", [RedisConnectionError, RedisTimeoutError, ResponseError]
)
async def test_storage_failure_returns_sanitized_unavailable(
    unary_channel: tuple[grpc_aio.Channel, MASServer],
    monkeypatch: pytest.MonkeyPatch,
    rpc_method: str,
    runtime_method: str,
    error_type: type[Exception],
) -> None:
    channel, runtime = unary_channel
    operation = AsyncMock(
        side_effect=error_type("redis://private-host:6379/internal-secret")
    )
    monkeypatch.setattr(runtime, runtime_method, operation)
    call = channel.unary_unary(f"/mas.runtime.v1.RuntimeService/{rpc_method}")
    payload = (
        mas_pb2.UpdateStateRequest(expected_revision=0).SerializeToString()
        if rpc_method == "UpdateState"
        else mas_pb2.ResetStateRequest(expected_revision=0).SerializeToString()
        if rpc_method == "ResetState"
        else b""
    )
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call(payload, timeout=2)
    assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
    assert exc.value.details() == "storage_unavailable"
    operation.assert_awaited_once()


@pytest.mark.parametrize(("rpc_method", "runtime_method"), _UNARY_METHODS[:4])
async def test_domain_errors_keep_existing_rpc_status(
    unary_channel: tuple[grpc_aio.Channel, MASServer],
    monkeypatch: pytest.MonkeyPatch,
    rpc_method: str,
    runtime_method: str,
) -> None:
    channel, runtime = unary_channel
    monkeypatch.setattr(
        runtime,
        runtime_method,
        AsyncMock(side_effect=InvalidArgumentError("invalid_json")),
    )
    call = channel.unary_unary(f"/mas.runtime.v1.RuntimeService/{rpc_method}")
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call(b"", timeout=2)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc.value.details() == "invalid_json"


async def test_programming_error_is_not_reported_as_storage_outage(
    unary_channel: tuple[grpc_aio.Channel, MASServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel, runtime = unary_channel
    monkeypatch.setattr(
        runtime, "get_state", AsyncMock(side_effect=ValueError("bad contract"))
    )
    call = channel.unary_unary("/mas.runtime.v1.RuntimeService/GetState")
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call(b"", timeout=2)
    assert exc.value.code() == grpc.StatusCode.UNKNOWN


@pytest.mark.parametrize(
    ("error", "status", "details"),
    [
        (
            RedisConnectionError("redis://private-host:6379/internal-secret"),
            grpc.StatusCode.UNAVAILABLE,
            "storage_unavailable",
        ),
        (
            InvalidArgumentError("invalid_instance_id"),
            grpc.StatusCode.INVALID_ARGUMENT,
            "invalid_instance_id",
        ),
    ],
)
async def test_transport_connection_preserves_domain_status_and_sanitizes_storage(
    unary_channel: tuple[grpc_aio.Channel, MASServer],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: grpc.StatusCode,
    details: str,
) -> None:
    channel, runtime = unary_channel
    operation = AsyncMock(side_effect=error)
    monkeypatch.setattr(runtime, "connect_session", operation)
    call = channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
    await call.write(
        mas_pb2.ClientEvent(
            hello=mas_pb2.Hello(instance_id="worker-inst")
        ).SerializeToString()
    )
    await call.done_writing()
    with pytest.raises(grpc_aio.AioRpcError) as exc:
        await call.read()
    assert exc.value.code() == status
    assert exc.value.details() == details
    operation.assert_awaited_once()


async def _idle_delivery() -> None:
    await asyncio.Event().wait()


def _delivery_task(
    _agent_id: str,
    _instance_id: str,
    _outbound: asyncio.Queue[OutboundDelivery],
    _inflight: dict[str, InflightDelivery],
) -> asyncio.Task[None]:
    return asyncio.create_task(_idle_delivery())


@pytest.mark.parametrize("invalid_event", [False, True])
async def test_transport_storage_failure_after_disconnect_keeps_cleanup_complete(
    redis: Redis,
    unary_channel: tuple[grpc_aio.Channel, MASServer],
    monkeypatch: pytest.MonkeyPatch,
    invalid_event: bool,
) -> None:
    channel, runtime = unary_channel
    agents = {
        "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={})
    }
    sessions = SessionManager(agents=agents, redis=redis)
    session = await sessions.connect(
        agent_id="worker", instance_id="worker-inst", task_factory=_delivery_task
    )
    registry = RegistryService(redis=redis, agents=agents)
    runtime._sessions = sessions
    runtime._registry = registry
    monkeypatch.setattr(runtime, "connect_session", AsyncMock(return_value=session))
    operation = AsyncMock(side_effect=RedisConnectionError("private backend endpoint"))
    monkeypatch.setattr(sessions.leases, "release", operation)
    try:
        call = channel.stream_stream("/mas.runtime.v1.RuntimeService/Transport")()
        await call.write(
            mas_pb2.ClientEvent(
                hello=mas_pb2.Hello(instance_id="worker-inst")
            ).SerializeToString()
        )
        welcome = await call.read()
        assert isinstance(welcome, bytes)
        assert mas_pb2.ServerEvent.FromString(welcome).HasField("welcome")
        if invalid_event:
            await call.write(b"")
        else:
            await call.done_writing()
        with pytest.raises(grpc_aio.AioRpcError) as exc:
            await call.read()
        assert exc.value.code() == (
            grpc.StatusCode.INVALID_ARGUMENT
            if invalid_event
            else grpc.StatusCode.UNAVAILABLE
        )
        assert exc.value.details() == (
            "expected_ack_or_nack" if invalid_event else "storage_unavailable"
        )
        async with asyncio.timeout(2):
            while (
                await sessions.snapshot()
                or not session.task.done()
                or not operation.await_count
            ):
                await asyncio.sleep(0.01)
        operation.assert_awaited_with(session.lease)
    finally:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
