"""Regression tests for reply authorization binding.

A reply must only resolve a pending request when it comes from the agent the
request was sent to; a spoofed reply (correct correlation id, wrong sender) must
not resolve the future. Replies with no pending request are buffered in the
bounded early-reply store (its bounds/TTL are covered in test_agent_early_replies).
"""

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


def _reply(sender_id: str, correlation_id: str) -> EnvelopeMessage:
    return EnvelopeMessage(
        message_id=f"reply-from-{sender_id}",
        sender_id=sender_id,
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )


def _delivery(msg: EnvelopeMessage, delivery_id: str) -> mas_pb2.Delivery:
    return mas_pb2.Delivery(
        delivery_id=delivery_id, envelope_json=msg.model_dump_json()
    )


class _RequestStub(mas_pb2_grpc.RuntimeServiceStub):
    """Minimal stub returning a fixed correlation id for Request."""

    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id

    async def Request(
        self,
        _request: mas_pb2.RequestRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.RequestResponse:
        del metadata
        return mas_pb2.RequestResponse(
            message_id="msg-1", correlation_id=self._correlation_id
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reply_from_request_target_resolves_pending() -> None:
    agent = Agent("sender")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[EnvelopeMessage] = loop.create_future()
    agent._pending_requests["corr-1"] = PendingRequest(
        future=fut, target_id="responder"
    )

    await agent._handle_delivery(_delivery(_reply("responder", "corr-1"), "d1"))

    assert fut.done()
    assert fut.result().sender_id == "responder"
    assert "corr-1" not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_spoofed_reply_does_not_resolve_and_legit_still_works() -> None:
    agent = Agent("sender")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[EnvelopeMessage] = loop.create_future()
    agent._pending_requests["corr-2"] = PendingRequest(
        future=fut, target_id="responder"
    )

    # Attacker knows the correlation id but is not the request target.
    await agent._handle_delivery(_delivery(_reply("attacker", "corr-2"), "d2"))

    assert not fut.done()
    # Pending request is left intact so the real responder can still answer.
    assert "corr-2" in agent._pending_requests

    await agent._handle_delivery(_delivery(_reply("responder", "corr-2"), "d3"))

    assert fut.done()
    assert fut.result().sender_id == "responder"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reply_without_pending_request_is_buffered() -> None:
    # Models a late reply that arrives after its request timed out: it lands in
    # the bounded early-reply buffer rather than leaking on a per-call dict.
    agent = Agent("sender")

    await agent._handle_delivery(_delivery(_reply("responder", "corr-late"), "d4"))

    assert "corr-late" in agent._early_replies


@pytest.mark.unit
@pytest.mark.asyncio
async def test_spoofed_early_reply_is_dropped_by_request() -> None:
    agent = Agent("sender")
    agent._stub = _RequestStub("corr-early")
    # An early reply was buffered from the wrong sender.
    agent._early_replies["corr-early"] = _reply("attacker", "corr-early")

    with pytest.raises(asyncio.TimeoutError):
        await agent.request("responder", "test", {}, timeout=0.05)

    assert "corr-early" not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legit_early_reply_is_accepted_by_request() -> None:
    agent = Agent("sender")
    agent._stub = _RequestStub("corr-early")
    agent._early_replies["corr-early"] = _reply("responder", "corr-early")

    response = await agent.request("responder", "test", {}, timeout=1.0)

    assert response.sender_id == "responder"
    assert "corr-early" not in agent._pending_requests
