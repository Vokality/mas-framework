"""Test simplified peer-to-peer messaging."""

import asyncio
from typing import override
import pytest
from mas import Agent, AgentMessage
from mas.gateway import GatewayService
from mas.gateway.config import GatewaySettings, FeaturesSettings

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


class ReceiverAgent(Agent):
    """Test agent that collects received messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages: list[AgentMessage] = []
        self.message_event = asyncio.Event()

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """Store received messages."""
        self.messages.append(message)
        self.message_event.set()


@pytest.mark.asyncio
async def test_peer_to_peer_messaging(mas_service):
    """Test direct peer-to-peer messaging between agents."""
    # Start gateway (streams, signing disabled for tests)
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
    # Create agents
    agent_a = ReceiverAgent("agent_a", capabilities=["send"])
    agent_b = ReceiverAgent("agent_b", capabilities=["receive"])

    await agent_a.start()
    await agent_b.start()

    # Allow A <-> B
    await gateway.auth_manager().allow_bidirectional(agent_a.id, agent_b.id)

    # Send message from A to B
    await agent_a.send("agent_b", "test.message", {"test": "data", "number": 42})

    # Wait for delivery
    await asyncio.wait_for(agent_b.message_event.wait(), timeout=2.0)

    # Verify message received
    assert len(agent_b.messages) == 1
    message = agent_b.messages[0]
    assert message.data["test"] == "data"
    assert message.data["number"] == 42
    assert message.sender_id == "agent_a"
    assert message.target_id == "agent_b"

    # Cleanup
    await agent_a.stop()
    await agent_b.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_bidirectional_messaging(mas_service):
    """Test bidirectional messaging between agents."""
    agent_a = ReceiverAgent("agent_a", capabilities=["chat"])
    agent_b = ReceiverAgent("agent_b", capabilities=["chat"])

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

    await agent_a.start()
    await agent_b.start()

    await gateway.auth_manager().allow_bidirectional(agent_a.id, agent_b.id)

    # A sends to B
    await agent_a.send("agent_b", "chat.message", {"from_a": "hello"})
    await asyncio.wait_for(agent_b.message_event.wait(), timeout=2.0)

    # B sends back to A
    agent_b.message_event.clear()
    await agent_b.send("agent_a", "chat.message", {"from_b": "world"})
    await asyncio.wait_for(agent_a.message_event.wait(), timeout=2.0)

    # Verify both received messages
    assert len(agent_a.messages) == 1
    assert agent_a.messages[0].data["from_b"] == "world"

    assert len(agent_b.messages) == 1
    assert agent_b.messages[0].data["from_a"] == "hello"

    await agent_a.stop()
    await agent_b.stop()
    await gateway.stop()


@pytest.mark.asyncio
async def test_discovery_by_capability(mas_service):
    """Test agent discovery by capabilities."""
    # Create agents with different capabilities
    agent_nlp = Agent("agent_nlp", capabilities=["nlp", "text"])
    agent_vision = Agent("agent_vision", capabilities=["vision", "image"])
    agent_math = Agent("agent_math", capabilities=["math", "calculation"])

    await agent_nlp.start()
    await agent_vision.start()
    await agent_math.start()

    # Wait a bit for registration
    await asyncio.sleep(0.1)

    # Discover agents with "nlp" capability
    nlp_agents = await agent_math.discover(capabilities=["nlp"])
    assert len(nlp_agents) == 1
    assert nlp_agents[0]["id"] == "agent_nlp"
    assert "nlp" in nlp_agents[0]["capabilities"]

    # Discover agents with "vision" capability
    vision_agents = await agent_math.discover(capabilities=["vision"])
    assert len(vision_agents) == 1
    assert vision_agents[0]["id"] == "agent_vision"

    # Discover all agents (no capability filter)
    all_agents = await agent_math.discover()
    assert len(all_agents) == 3
    agent_ids = [a["id"] for a in all_agents]
    assert "agent_nlp" in agent_ids
    assert "agent_vision" in agent_ids
    assert "agent_math" in agent_ids

    await agent_nlp.stop()
    await agent_vision.stop()
    await agent_math.stop()


@pytest.mark.asyncio
async def test_state_persistence(mas_service):
    """Test that agent state is persisted to Redis."""
    # Create agent and update state
    agent = Agent("stateful_agent", capabilities=["storage"])
    await agent.start()

    # Update state
    await agent.update_state({"counter": 42, "name": "test"})

    # Verify state updated (in-memory keeps original types)
    assert agent.state["counter"] == 42
    assert agent.state["name"] == "test"

    # Stop and restart agent
    await agent.stop()

    # Wait a bit for cleanup
    await asyncio.sleep(0.1)

    # Create new instance with same ID
    agent2 = Agent("stateful_agent", capabilities=["storage"])
    await agent2.start()

    # Verify state persisted (Redis returns strings)
    assert agent2.state is not None
    assert "counter" in agent2.state, f"State: {agent2.state}"
    assert agent2.state["counter"] == "42"  # Redis stores/loads as string
    assert agent2.state["name"] == "test"

    await agent2.stop()


@pytest.mark.asyncio
async def test_multiple_messages(mas_service):
    """Test handling multiple messages in rapid succession."""
    agent_a = ReceiverAgent("sender", capabilities=["send"])
    agent_b = ReceiverAgent("receiver", capabilities=["receive"])

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

    await agent_a.start()
    await agent_b.start()

    await gateway.auth_manager().allow_bidirectional(agent_a.id, agent_b.id)

    # Send 10 messages rapidly
    num_messages = 10
    for i in range(num_messages):
        await agent_a.send("receiver", "test.message", {"index": i})

    # Wait for all messages
    await asyncio.sleep(1.0)

    # Verify all received
    assert len(agent_b.messages) == num_messages

    # Verify order and content
    for i, msg in enumerate(agent_b.messages):
        assert msg.data["index"] == i

    await agent_a.stop()
    await agent_b.stop()
    await gateway.stop()
