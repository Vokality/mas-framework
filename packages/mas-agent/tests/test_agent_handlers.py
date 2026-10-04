"""Regression coverage for handler inheritance and retry decisions."""

from __future__ import annotations

import pytest
from mas_agent.agent import Agent
from mas_core.protocol import EnvelopeMessage
from pydantic import BaseModel


@pytest.mark.asyncio
async def test_subclass_registration_overrides_inherited_message_type() -> None:
    calls: list[str] = []

    class Parent(Agent):
        @Agent.on("ping")
        async def z_parent(self, message: EnvelopeMessage, payload: None) -> None:
            calls.append("parent")

    class Child(Parent):
        @Agent.on("ping")
        async def a_child(self, message: EnvelopeMessage, payload: None) -> None:
            calls.append("child")

    await Child("worker")._dispatch_typed(
        EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="ping", data={}
        )
    )
    assert calls == ["child"]


@pytest.mark.asyncio
async def test_undecorated_method_override_disables_inherited_handler() -> None:
    class Parent(Agent):
        @Agent.on("ping")
        async def ping(self, message: EnvelopeMessage, payload: None) -> None:
            raise AssertionError("shadowed handler must not be invoked")

    class Child(Parent):
        async def ping(self, message: EnvelopeMessage, payload: None) -> None:
            return

    assert not await Child("worker")._dispatch_typed(
        EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="ping", data={}
        )
    )


@pytest.mark.asyncio
async def test_runtime_handler_failure_is_retryable() -> None:
    class FailingAgent(Agent):
        async def on_message(self, message: EnvelopeMessage) -> None:
            raise ConnectionError("temporary upstream failure")

    agent = FailingAgent("worker")
    await agent._handle_message_and_ack(
        "delivery",
        EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="ping", data={}
        ),
        parent_context=None,
    )
    event = agent._outgoing.get_nowait()
    assert event.HasField("nack")
    assert event.nack.retryable


@pytest.mark.asyncio
async def test_invalid_payload_is_not_retryable() -> None:
    class Payload(BaseModel):
        count: int

    class Worker(Agent):
        @Agent.on("ping", model=Payload)
        async def ping(self, message: EnvelopeMessage, payload: Payload) -> None:
            raise AssertionError("invalid payload must never reach handler")

    agent = Worker("worker")
    await agent._handle_message_and_ack(
        "delivery",
        EnvelopeMessage(
            sender_id="sender",
            target_id="worker",
            message_type="ping",
            data={"count": "invalid"},
        ),
        parent_context=None,
    )
    event = agent._outgoing.get_nowait()
    assert event.HasField("nack")
    assert not event.nack.retryable


@pytest.mark.asyncio
async def test_validation_error_inside_handler_remains_retryable() -> None:
    class UpstreamResponse(BaseModel):
        count: int

    class Worker(Agent):
        async def on_message(self, message: EnvelopeMessage) -> None:
            UpstreamResponse.model_validate({"count": "temporary invalid response"})

    agent = Worker("worker")
    await agent._handle_message_and_ack(
        "delivery",
        EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="ping", data={}
        ),
        parent_context=None,
    )
    assert agent._outgoing.get_nowait().nack.retryable
