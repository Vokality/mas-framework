"""Regression tests for Agent request correlation cleanup."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from mas._proto.v1 import mas_pb2, mas_pb2_grpc
from mas.agent import Agent
from mas.protocol import EnvelopeMessage, MessageMeta


class _RequestStub:
    """Minimal stub implementing the Request RPC used by Agent.request()."""

    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id

    async def Request(
        self,
        _request: mas_pb2.RequestRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.RequestResponse:
        del metadata
        return mas_pb2.RequestResponse(
            message_id="msg-1",
            correlation_id=self._correlation_id,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_timeout_cleans_pending_correlation() -> None:
    agent: Agent[dict[str, object]] = Agent("sender")
    correlation_id = "corr-timeout"
    agent._stub = cast(mas_pb2_grpc.MasServiceStub, _RequestStub(correlation_id))

    with pytest.raises(asyncio.TimeoutError):
        await agent.request("target", "test", {}, timeout=0.01)

    assert correlation_id not in agent._pending_requests


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_early_reply_cleans_pending_correlation() -> None:
    agent: Agent[dict[str, object]] = Agent("sender")
    correlation_id = "corr-early"
    agent._stub = cast(mas_pb2_grpc.MasServiceStub, _RequestStub(correlation_id))
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
