"""Regression tests for Agent request correlation cleanup."""

from __future__ import annotations

import asyncio

import pytest
from mas_agent import Agent
from mas_agent._core import PendingRequest
from mas_core import EnvelopeMessage, MessageMeta
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)
from mas_proto.runtime.v1 import (
    runtime_pb2_grpc as mas_pb2_grpc,
)


class _RequestStub(mas_pb2_grpc.RuntimeServiceStub):
    """Minimal stub implementing the Request RPC used by Agent.request()."""

    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id
        self.timeout_ms: int | None = None

    async def Request(
        self,
        request: mas_pb2.RequestRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.RequestResponse:
        del metadata
        self.timeout_ms = request.timeout_ms
        return mas_pb2.RequestResponse(
            message_id="msg-1",
            correlation_id=self._correlation_id,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_timeout_cleans_pending_correlation() -> None:
    agent = Agent("sender")
    correlation_id = "corr-timeout"
    agent._stub = _RequestStub(correlation_id)

    with pytest.raises(asyncio.TimeoutError):
        await agent.request("target", "test", {}, timeout=0.01)

    assert correlation_id not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_timeout_includes_rpc_submission() -> None:
    class SlowStub(_RequestStub):
        async def Request(
            self,
            request: mas_pb2.RequestRequest,
            metadata: list[tuple[str, str]] | None = None,
        ) -> mas_pb2.RequestResponse:
            await asyncio.sleep(0.1)
            return await super().Request(request, metadata)

    agent = Agent("sender")
    agent._stub = SlowStub("slow")
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError):
        await agent.request("target", "test", {}, timeout=0.01)
    assert loop.time() - started < 0.08
    assert not agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_resolves_pending_requests_promptly() -> None:
    agent = Agent("sender")
    agent._stub = _RequestStub("stopped")
    request = asyncio.create_task(agent.request("target", "test", {}, timeout=1))
    await asyncio.sleep(0)
    await agent.stop()
    assert not agent._pending_requests
    with pytest.raises(ConnectionError, match="stopped"):
        await request


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_early_reply_cleans_pending_correlation() -> None:
    agent = Agent("sender")
    correlation_id = "corr-early"
    agent._stub = _RequestStub(correlation_id)
    agent._early_replies[correlation_id] = EnvelopeMessage(
        message_id="reply-1",
        sender_id="target",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )

    response = await agent.request("target", "test", {}, timeout=1.0)

    assert response.message_id == "reply-1"
    assert correlation_id not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_default_timeout_is_sent_to_server() -> None:
    agent = Agent("sender")
    correlation_id = "corr-default-timeout"
    stub = _RequestStub(correlation_id)
    agent._stub = stub
    agent._early_replies[correlation_id] = EnvelopeMessage(
        message_id="reply-1",
        sender_id="target",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )

    await agent.request("target", "test", {})

    assert stub.timeout_ms == 60_000
    assert correlation_id not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_drops_spoofed_early_reply() -> None:
    agent = Agent("sender")
    correlation_id = "corr-spoofed-early"
    agent._stub = _RequestStub(correlation_id)
    agent._early_replies[correlation_id] = EnvelopeMessage(
        message_id="reply-1",
        sender_id="attacker",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )

    with pytest.raises(asyncio.TimeoutError):
        await agent.request("target", "test", {}, timeout=0.01)

    assert correlation_id not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_drops_spoofed_pending_reply() -> None:
    agent = Agent("sender")
    correlation_id = "corr-spoofed-pending"
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    agent._pending_requests[correlation_id] = PendingRequest(
        future=fut,
        target_id="target",
    )

    spoofed = EnvelopeMessage(
        message_id="spoofed",
        sender_id="attacker",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )
    await agent._handle_delivery(
        mas_pb2.Delivery(
            delivery_id="d-spoofed",
            envelope_json=spoofed.model_dump_json(),
        )
    )

    assert not fut.done()
    assert correlation_id in agent._pending_requests

    legitimate = EnvelopeMessage(
        message_id="legitimate",
        sender_id="target",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )
    await agent._handle_delivery(
        mas_pb2.Delivery(
            delivery_id="d-legitimate",
            envelope_json=legitimate.model_dump_json(),
        )
    )

    assert fut.result().message_id == "legitimate"
    assert correlation_id not in agent._pending_requests
