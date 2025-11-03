"""Tests for request-response pattern in Agent SDK."""

import asyncio
from typing import override
import pytest
from mas import Agent, AgentMessage, MASService


class ResponderAgent(Agent):
    """Test agent that responds to requests."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id, capabilities=["responder"], redis_url=redis_url
        )
        self.requests_handled = 0

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """Handle messages - reply if it's a request."""
        if message.expects_reply:
            # Simulate some processing
            await asyncio.sleep(0.1)

            # Reply with processed result
            await message.reply(
                {
                    "result": f"Processed: {message.payload.get('data')}",
                    "request_count": self.requests_handled,
                }
            )

            self.requests_handled += 1


class RequesterAgent(Agent):
    """Test agent that makes requests."""

    def __init__(self, agent_id: str, redis_url: str):
        super().__init__(
            agent_id=agent_id, capabilities=["requester"], redis_url=redis_url
        )
        self.responder_id: str | None = None


@pytest.mark.asyncio
async def test_basic_request_response():
    """Test basic request-response pattern."""
    service = MASService(redis_url="redis://localhost:6379")
    await service.start()

    responder = ResponderAgent("responder_1", "redis://localhost:6379")
    requester = RequesterAgent("requester_1", "redis://localhost:6379")

    try:
        await responder.start()
        await requester.start()

        # Make a request
        response = await requester.request(
            responder.id, {"data": "test_value"}, timeout=5.0
        )

        # Verify response
        assert response.payload.get("result") == "Processed: test_value"
        assert response.payload.get("request_count") == 0
        assert responder.requests_handled == 1

    finally:
        await requester.stop()
        await responder.stop()
        await service.stop()


@pytest.mark.asyncio
async def test_concurrent_requests():
    """Test multiple concurrent requests from same agent."""
    service = MASService(redis_url="redis://localhost:6379")
    await service.start()

    responder = ResponderAgent("responder_2", "redis://localhost:6379")
    requester = RequesterAgent("requester_2", "redis://localhost:6379")

    try:
        await responder.start()
        await requester.start()

        # Make multiple concurrent requests
        tasks = [
            requester.request(responder.id, {"data": f"request_{i}"}, timeout=5.0)
            for i in range(5)
        ]

        responses = await asyncio.gather(*tasks)

        # Verify all responses
        assert len(responses) == 5
        for response in responses:
            assert "Processed: request_" in response.payload.get("result", "")

        assert responder.requests_handled == 5

    finally:
        await requester.stop()
        await responder.stop()
        await service.stop()


@pytest.mark.asyncio
async def test_request_timeout():
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

    service = MASService(redis_url="redis://localhost:6379")
    await service.start()

    slow_responder = SlowResponderAgent("slow_responder", "redis://localhost:6379")
    requester = RequesterAgent("requester_3", "redis://localhost:6379")

    try:
        await slow_responder.start()
        await requester.start()

        # Request should timeout
        with pytest.raises(asyncio.TimeoutError):
            await requester.request(
                slow_responder.id,
                {"data": "test"},
                timeout=0.5,  # Short timeout
            )

    finally:
        await requester.stop()
        await slow_responder.stop()
        await service.stop()


@pytest.mark.asyncio
async def test_reply_without_request():
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
                    await message.reply({"data": "bad"})

    service = MASService(redis_url="redis://localhost:6379")
    await service.start()

    bad_responder = BadResponderAgent("bad_responder", "redis://localhost:6379")
    sender = Agent(
        "sender", capabilities=["sender"], redis_url="redis://localhost:6379"
    )

    try:
        await bad_responder.start()
        await sender.start()

        # Send regular message (not a request)
        await sender.send(bad_responder.id, {"data": "test"})

        # Give time for message to be processed
        await asyncio.sleep(0.5)

    finally:
        await sender.stop()
        await bad_responder.stop()
        await service.stop()


@pytest.mark.asyncio
async def test_request_response_preserves_data():
    """Test that all payload data is preserved in request/response."""
    service = MASService(redis_url="redis://localhost:6379")
    await service.start()

    responder = ResponderAgent("responder_4", "redis://localhost:6379")
    requester = RequesterAgent("requester_4", "redis://localhost:6379")

    try:
        await responder.start()
        await requester.start()

        # Send complex payload
        payload = {
            "string": "test",
            "number": 42,
            "list": [1, 2, 3],
            "nested": {"key": "value"},
        }

        response = await requester.request(responder.id, payload, timeout=5.0)

        # Verify internal fields are not exposed
        assert (
            "_correlation_id" not in response.payload
            or response.payload.get("_correlation_id") is not None
        )
        assert (
            "_is_reply" not in response.payload
            or response.payload.get("_is_reply") is True
        )

        # Verify response data
        assert response.payload.get("result") is not None

    finally:
        await requester.stop()
        await responder.stop()
        await service.stop()
