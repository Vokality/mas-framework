"""Validated reply receipts are observable without changing immediate ACKs."""

from __future__ import annotations

import asyncio

import pytest
from mas_agent import Agent
from mas_agent import transport as transport_module
from mas_agent._core import PendingRequest
from mas_core.protocol import EnvelopeMessage, MessageMeta
from mas_core.telemetry.runtime import TelemetryRuntime
from mas_proto.runtime.v1 import runtime_pb2
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from pytest import MonkeyPatch


@pytest.mark.asyncio
async def test_reply_receipt_records_parent_trace_and_immediate_resolution(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = TracerProvider()
    runtime = TelemetryRuntime(
        enabled=True,
        tracer=provider.get_tracer("test"),
        tracer_provider=provider,
        meter_provider=MeterProvider(),
    )
    monkeypatch.setattr(transport_module, "get_telemetry", lambda: runtime)
    agent = Agent("requester")
    future: asyncio.Future[EnvelopeMessage] = asyncio.get_running_loop().create_future()
    agent._pending_requests["correlation"] = PendingRequest(
        future=future,
        target_id="responder",
    )
    message = EnvelopeMessage(
        message_id="reply",
        sender_id="responder",
        target_id="requester",
        message_type="result",
        data={},
        meta=MessageMeta(
            is_reply=True,
            correlation_id="correlation",
            traceparent="00-0000000000000000000000000000007b-00000000000001c8-01",
        ),
    )
    try:
        await agent._handle_delivery(
            runtime_pb2.Delivery(
                delivery_id="delivery",
                envelope_json=message.model_dump_json(),
            )
        )
        assert future.done() and future.result().message_id == "reply"
        assert agent._outgoing.get_nowait().ack.delivery_id == "delivery"
        (record,) = await runtime.drain_spans()
        assert record.name == "mas.agent.transport.receive"
        assert record.trace_id == "0000000000000000000000000000007b"
        assert record.parent_span_id == "00000000000001c8"
        assert record.attributes["mas.is_reply"] is True
        assert record.attributes["mas.message_id"] == "reply"
        assert not agent._handler_tasks
    finally:
        await runtime.shutdown()
