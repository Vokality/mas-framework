"""Tests for decorator-based typed message dispatch."""

import asyncio
from typing import override
import pytest
from pydantic import BaseModel
from mas import Agent, AgentMessage
from mas.gateway import GatewayService
from mas.gateway.config import GatewaySettings, FeaturesSettings


# Test Pydantic models for typed messages
class EchoRequest(BaseModel):
    """Request model for echo messages."""

    text: str
    count: int = 1


class EchoResponse(BaseModel):
    """Response model for echo messages."""

    echoed: str
    request_count: int


class GreetingRequest(BaseModel):
    """Request model for greeting messages."""

    name: str
    language: str = "en"


class TypedResponderState(BaseModel):
    """State model for TypedResponderAgent."""

    echo_count: int = 0
    greeting_count: int = 0


class TypedResponderAgent(Agent):
    """Test agent with decorator-based typed handlers."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id,
            capabilities=["typed_responder"],
            redis_url=redis_url,
            state_model=TypedResponderState,
        )
        self.unhandled_messages: list[AgentMessage] = []

    @Agent.on("echo.request", model=EchoRequest)
    async def handle_echo(self, msg: AgentMessage, payload: EchoRequest) -> None:
        """Handle echo requests with typed payload."""
        # Get current echo count from typed state
        echo_count = self.state.echo_count + 1

        echoed_text = " ".join([payload.text] * payload.count)
        await msg.reply(
            "echo.response",
            EchoResponse(echoed=echoed_text, request_count=echo_count).model_dump(),
        )

        # Update state with incremented counter
        await self.update_state({"echo_count": echo_count})

    @Agent.on("greeting.request", model=GreetingRequest)
    async def handle_greeting(
        self, msg: AgentMessage, payload: GreetingRequest
    ) -> None:
        """Handle greeting requests with typed payload."""
        # Get current greeting count from typed state
        greeting_count = self.state.greeting_count + 1

        greeting = (
            f"Hello, {payload.name}!"
            if payload.language == "en"
            else f"Hola, {payload.name}!"
        )
        await msg.reply(
            "greeting.response", {"greeting": greeting, "count": greeting_count}
        )

        # Update state with incremented counter
        await self.update_state({"greeting_count": greeting_count})

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """Fallback handler for unhandled message types."""
        self.unhandled_messages.append(message)


class TypedRequesterAgent(Agent):
    """Test agent that makes typed requests."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id, capabilities=["typed_requester"], redis_url=redis_url
        )


@pytest.mark.asyncio
async def test_decorator_based_dispatch(mas_service):
    """Test that decorator-based handlers are called correctly."""
    responder = TypedResponderAgent("typed_responder_1", "redis://localhost:6379")
    requester = TypedRequesterAgent("typed_requester_1", "redis://localhost:6379")

    settings = GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()

    await responder.start()
    await requester.start()

    await gateway.auth_manager().allow_bidirectional(requester.id, responder.id)

    # Make a typed request
    response = await requester.request(
        responder.id,
        "echo.request",
        EchoRequest(text="hello", count=3).model_dump(),
        timeout=5.0,
    )

    # Verify response
    assert response.message_type == "echo.response"
    assert response.data["echoed"] == "hello hello hello"
    assert response.data["request_count"] == 1
    # State persists the count (typed)
    assert responder.state.echo_count == 1

    await requester.stop()
    await responder.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_pydantic_validation(mas_service):
    """Test that Pydantic models validate message data."""
    responder = TypedResponderAgent("typed_responder_2", "redis://localhost:6379")
    requester = TypedRequesterAgent("typed_requester_2", "redis://localhost:6379")

    settings = GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()

    await responder.start()
    await requester.start()

    await gateway.auth_manager().allow_bidirectional(requester.id, responder.id)

    # Send invalid data (missing required field)
    await requester.send(
        responder.id,
        "echo.request",
        {"count": 3},  # Missing 'text' field
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Handler should not be called due to validation error
    # State should remain unchanged (typed)
    assert responder.state.echo_count == 0
    # Validation errors are caught and logged, but don't fall back to on_message
    # (this is current behavior - validation errors are fatal)

    await requester.stop()
    await responder.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_multiple_typed_handlers(mas_service):
    """Test that multiple typed handlers work correctly."""
    responder = TypedResponderAgent("typed_responder_3", "redis://localhost:6379")
    requester = TypedRequesterAgent("typed_requester_3", "redis://localhost:6379")

    settings = GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()

    await responder.start()
    await requester.start()

    await gateway.auth_manager().allow_bidirectional(requester.id, responder.id)

    # Send echo request
    echo_response = await requester.request(
        responder.id,
        "echo.request",
        EchoRequest(text="test", count=2).model_dump(),
        timeout=5.0,
    )

    # Send greeting request
    greeting_response = await requester.request(
        responder.id,
        "greeting.request",
        GreetingRequest(name="Alice", language="en").model_dump(),
        timeout=5.0,
    )

    # Verify both handlers were called
    assert echo_response.data["echoed"] == "test test"
    assert echo_response.data["request_count"] == 1
    assert greeting_response.data["greeting"] == "Hello, Alice!"
    assert greeting_response.data["count"] == 1

    # State persists both counts (typed)
    assert responder.state.echo_count == 1
    assert responder.state.greeting_count == 1

    await requester.stop()
    await responder.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_fallback_to_on_message(mas_service):
    """Test that unhandled message types fall back to on_message."""
    responder = TypedResponderAgent("typed_responder_4", "redis://localhost:6379")
    requester = TypedRequesterAgent("typed_requester_4", "redis://localhost:6379")

    settings = GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()

    await responder.start()
    await requester.start()

    await gateway.auth_manager().allow_bidirectional(requester.id, responder.id)

    # Send unhandled message type
    await requester.send(
        responder.id,
        "unknown.message",
        {"data": "test"},
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Should fall back to on_message
    assert len(responder.unhandled_messages) == 1
    assert responder.unhandled_messages[0].message_type == "unknown.message"
    assert responder.unhandled_messages[0].data["data"] == "test"

    # Typed handlers should not be called
    # State should remain unchanged (typed)
    assert responder.state.echo_count == 0
    assert responder.state.greeting_count == 0

    await requester.stop()
    await responder.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_handler_without_model(mas_service):
    """Test handler registration without Pydantic model."""

    class SimpleTypedAgentState(BaseModel):
        """State model for SimpleTypedAgent."""

        handled: bool = False

    class SimpleTypedAgent(Agent):
        """Agent with handler without model."""

        def __init__(self, agent_id: str, redis_url: str):
            super().__init__(
                agent_id=agent_id,
                capabilities=["simple"],
                redis_url=redis_url,
                state_model=SimpleTypedAgentState,
            )

        @Agent.on("simple.message")
        async def handle_simple(self, msg: AgentMessage, payload: None) -> None:
            """Handle simple message without model."""
            await self.update_state({"handled": True})
            assert payload is None

    responder = SimpleTypedAgent("simple_responder", "redis://localhost:6379")
    requester = TypedRequesterAgent("simple_requester", "redis://localhost:6379")

    settings = GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()

    await responder.start()
    await requester.start()

    await gateway.auth_manager().allow_bidirectional(requester.id, responder.id)

    # Send message
    await requester.send(responder.id, "simple.message", {"test": "data"})

    # Wait for processing
    await asyncio.sleep(0.5)

    # Handler should be called and state updated (typed)
    assert responder.state.handled is True

    await requester.stop()
    await responder.stop()
    await gateway.stop()
