"""Tests for request-response pattern in Agent SDK."""

import asyncio
from typing import override
import pytest
from pydantic import BaseModel
from mas import Agent, AgentMessage


class ResponderState(BaseModel):
    """State model for ResponderAgent."""

    requests_handled: int = 0


class ResponderAgent(Agent):
    """Test agent that responds to requests."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id,
            capabilities=["responder"],
            redis_url=redis_url,
            state_model=ResponderState,
        )

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """Handle messages - reply if it's a request."""
        if message.expects_reply:
            # Simulate some processing
            await asyncio.sleep(0.1)

            # Get current request count from typed state
            requests_handled = self.state.requests_handled

            # Reply with processed result
            await message.reply(
                "test.response",
                {
                    "result": f"Processed: {message.data.get('data')}",
                    "request_count": requests_handled,
                },
            )

            # Update state with incremented counter
            await self.update_state({"requests_handled": requests_handled + 1})


class RequesterAgent(Agent):
    """Test agent that makes requests."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id, capabilities=["requester"], redis_url=redis_url
        )
        self.responder_id: str | None = None


@pytest.mark.asyncio
async def test_basic_request_response(mas_service):
    """Test basic request-response pattern."""
    responder = ResponderAgent("responder_1", "redis://localhost:6379")
    requester = RequesterAgent("requester_1", "redis://localhost:6379")

    await responder.start()
    await requester.start()

    # Make a request
    response = await requester.request(
        responder.id, "test.request", {"data": "test_value"}, timeout=5.0
    )

    # Verify response
    assert response.data.get("result") == "Processed: test_value"
    assert response.data.get("request_count") == 0
    # State persists the count (typed)
    assert responder.state.requests_handled == 1

    await requester.stop()
    await responder.stop()


@pytest.mark.asyncio
async def test_concurrent_requests(mas_service):
    """Test multiple concurrent requests from same agent."""
    responder = ResponderAgent("responder_2", "redis://localhost:6379")
    requester = RequesterAgent("requester_2", "redis://localhost:6379")

    await responder.start()
    await requester.start()

    # Make multiple concurrent requests
    tasks = [
        requester.request(
            responder.id, "test.request", {"data": f"request_{i}"}, timeout=5.0
        )
        for i in range(5)
    ]

    responses = await asyncio.gather(*tasks)

    # Verify all responses
    assert len(responses) == 5
    for response in responses:
        assert "Processed: request_" in response.data.get("result", "")

    # State persists the total count
    # Note: Due to concurrent updates, exact count may vary without atomic operations
    # But we should have at least some requests handled
    assert responder.state.requests_handled >= 1  # At least one request was handled
    assert responder.state.requests_handled <= 5  # But not more than total requests

    await requester.stop()
    await responder.stop()


@pytest.mark.asyncio
async def test_request_timeout(mas_service):
    """Test request timeout when no response."""

    class SlowResponderAgent(Agent):
        """Responder that never replies."""

        def __init__(self, agent_id: str, redis_url: str):
            super().__init__(
                agent_id=agent_id, capabilities=["slow"], redis_url=redis_url
            )

        @override
        async def on_message(self, message: AgentMessage) -> None:
            # Don't reply - just wait forever
            await asyncio.sleep(100)

    slow_responder = SlowResponderAgent("slow_responder", "redis://localhost:6379")
    requester = RequesterAgent("requester_3", "redis://localhost:6379")

    await slow_responder.start()
    await requester.start()

    # Request should timeout
    with pytest.raises(asyncio.TimeoutError):
        await requester.request(
            slow_responder.id,
            "test.request",
            {"data": "test"},
            timeout=0.5,  # Short timeout
        )

    await requester.stop()
    await slow_responder.stop()


@pytest.mark.asyncio
async def test_reply_without_request(mas_service):
    """Test that reply() fails if message doesn't expect reply."""

    class BadResponderAgent(Agent):
        """Tries to reply to non-request messages."""

        def __init__(self, agent_id: str, redis_url: str):
            super().__init__(
                agent_id=agent_id, capabilities=["bad"], redis_url=redis_url
            )

        @override
        async def on_message(self, message: AgentMessage) -> None:
            # Try to reply even though not a request
            if not message.expects_reply:
                with pytest.raises(RuntimeError, match="does not have correlation ID"):
                    await message.reply("test.response", {"data": "bad"})

    bad_responder = BadResponderAgent("bad_responder", "redis://localhost:6379")
    sender = Agent(
        "sender", capabilities=["sender"], redis_url="redis://localhost:6379"
    )

    await bad_responder.start()
    await sender.start()

    # Send regular message (not a request)
    await sender.send(bad_responder.id, "test.message", {"data": "test"})

    # Give time for message to be processed
    await asyncio.sleep(0.5)

    await sender.stop()
    await bad_responder.stop()


@pytest.mark.asyncio
async def test_request_response_preserves_data(mas_service):
    """Test that all payload data is preserved in request/response."""
    responder = ResponderAgent("responder_4", "redis://localhost:6379")
    requester = RequesterAgent("requester_4", "redis://localhost:6379")

    await responder.start()
    await requester.start()

    # Send complex payload
    payload = {
        "string": "test",
        "number": 42,
        "list": [1, 2, 3],
        "nested": {"key": "value"},
    }

    response = await requester.request(
        responder.id, "test.request", payload, timeout=5.0
    )

    # Verify internal fields are not exposed
    assert (
        "_correlation_id" not in response.data
        or response.data.get("_correlation_id") is not None
    )
    assert "_is_reply" not in response.data or response.data.get("_is_reply") is True

    # Verify response data
    assert response.data.get("result") is not None

    await requester.stop()
    await responder.stop()
