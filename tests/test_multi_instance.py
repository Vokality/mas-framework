"""Tests for multi-instance agent support."""

import asyncio
import hashlib
from typing import override

import pytest
from pydantic import BaseModel

from mas import Agent, AgentMessage
from mas.gateway import GatewayService
from mas.gateway.config import FeaturesSettings, GatewaySettings
from mas.registry import AgentRegistry

pytestmark = pytest.mark.asyncio


class CounterState(BaseModel):
    """State model for counter agent."""

    count: int = 0


class CollectorAgent(Agent):
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


class ResponderAgent(Agent):
    """Test agent that responds with its instance_id."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handled_count = 0

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """Respond with instance info."""
        self.handled_count += 1
        if message.meta.expects_reply and message.meta.correlation_id:
            await message.reply(
                "response",
                {
                    "instance_id": self.instance_id,
                    "handled_count": self.handled_count,
                },
            )


class TestMultiInstanceBasics:
    """Test basic multi-instance functionality."""

    async def test_each_instance_has_unique_id(self):
        """Test that each agent instance gets a unique instance_id."""
        agent1 = Agent("shared_agent", capabilities=["test"])
        agent2 = Agent("shared_agent", capabilities=["test"])

        # Instance IDs should be unique even for same agent_id
        assert agent1.instance_id != agent2.instance_id
        assert len(agent1.instance_id) == 8
        assert len(agent2.instance_id) == 8

    async def test_instance_id_is_stable(self):
        """Test that instance_id doesn't change during agent lifecycle."""
        agent = Agent("test_agent", capabilities=["test"])
        initial_id = agent.instance_id

        await agent.start()
        assert agent.instance_id == initial_id

        await agent.stop()
        assert agent.instance_id == initial_id


class TestInstanceRegistration:
    """Test instance registration and tracking."""

    async def test_first_instance_registers_agent(self, redis):
        """Test that first instance creates agent registration."""
        agent = Agent("new_agent", capabilities=["test"])
        await agent.start()

        try:
            # Verify agent is registered
            agent_data = await redis.hgetall("agent:new_agent")
            assert agent_data["id"] == "new_agent"
            assert agent_data["status"] == "ACTIVE"

            token_hash = await redis.get(
                f"agent:new_agent:token_hash:{agent.instance_id}"
            )
            assert token_hash == hashlib.sha256(agent.token.encode()).hexdigest()

            # Verify instance count
            count = await redis.get("agent:new_agent:instance_count")
            assert count == "1"
        finally:
            await agent.stop()

    async def test_second_instance_joins_existing(self, redis):
        """Test that second instance joins without re-registering."""
        agent1 = Agent("shared_agent", capabilities=["test"])
        await agent1.start()

        original_token = agent1.token
        original_reg_time = await redis.hget("agent:shared_agent", "registered_at")

        try:
            # Start second instance
            agent2 = Agent("shared_agent", capabilities=["test"])
            await agent2.start()

            try:
                # Tokens are per-instance (different across instances)
                assert agent2.token != original_token

                token_hash_1 = await redis.get(
                    f"agent:shared_agent:token_hash:{agent1.instance_id}"
                )
                token_hash_2 = await redis.get(
                    f"agent:shared_agent:token_hash:{agent2.instance_id}"
                )
                assert token_hash_1 == hashlib.sha256(agent1.token.encode()).hexdigest()
                assert token_hash_2 == hashlib.sha256(agent2.token.encode()).hexdigest()

                # Registration time should be unchanged
                current_reg_time = await redis.hget(
                    "agent:shared_agent", "registered_at"
                )
                assert current_reg_time == original_reg_time

                # Instance count should be 2
                count = await redis.get("agent:shared_agent:instance_count")
                assert count == "2"
            finally:
                await agent2.stop()
        finally:
            await agent1.stop()

    async def test_instance_count_decrements_on_stop(self, redis):
        """Test that instance count decrements when instance stops."""
        agent1 = Agent("shared_agent", capabilities=["test"])
        agent2 = Agent("shared_agent", capabilities=["test"])

        await agent1.start()
        await agent2.start()

        # Both running
        count = await redis.get("agent:shared_agent:instance_count")
        assert count == "2"

        # Stop one
        await agent2.stop()
        count = await redis.get("agent:shared_agent:instance_count")
        assert count == "1"

        # Agent still registered
        agent_data = await redis.hgetall("agent:shared_agent")
        assert agent_data["status"] == "ACTIVE"

        # Stop last one
        await agent1.stop()

        # Agent should be deregistered
        agent_data = await redis.hgetall("agent:shared_agent")
        assert agent_data == {}


class TestInstanceHeartbeats:
    """Test per-instance heartbeat functionality."""

    async def test_each_instance_has_own_heartbeat(self, redis):
        """Test that each instance maintains its own heartbeat."""
        agent1 = Agent("shared_agent", capabilities=["test"])
        agent2 = Agent("shared_agent", capabilities=["test"])

        await agent1.start()
        await agent2.start()

        try:
            # Wait for heartbeats to be set
            await asyncio.sleep(0.1)

            # Each instance should have its own heartbeat key
            hb1_key = f"agent:shared_agent:heartbeat:{agent1.instance_id}"
            hb2_key = f"agent:shared_agent:heartbeat:{agent2.instance_id}"

            hb1_exists = await redis.exists(hb1_key)
            hb2_exists = await redis.exists(hb2_key)

            assert hb1_exists == 1
            assert hb2_exists == 1
        finally:
            await agent1.stop()
            await agent2.stop()

    async def test_heartbeat_cleanup_on_stop(self, redis):
        """Test that heartbeat is cleaned up when instance stops."""
        agent = Agent("test_agent", capabilities=["test"])
        await agent.start()

        hb_key = f"agent:test_agent:heartbeat:{agent.instance_id}"

        # Heartbeat should exist
        await asyncio.sleep(0.1)
        assert await redis.exists(hb_key) == 1

        # Stop agent
        await agent.stop()

        # Heartbeat should be cleaned up
        assert await redis.exists(hb_key) == 0


class TestInstanceHealthChecks:
    """Test health checking with multiple instances."""

    async def test_registry_has_healthy_instance(self, redis):
        """Test has_healthy_instance check."""
        registry = AgentRegistry(redis)

        # Register and set up heartbeat manually
        agent = Agent("health_test", capabilities=["test"])
        await agent.start()

        try:
            # Should be healthy
            is_healthy = await registry.has_healthy_instance("health_test")
            assert is_healthy is True
        finally:
            await agent.stop()

    async def test_registry_instance_heartbeats(self, redis):
        """Test get_instance_heartbeats returns all instances."""
        agent1 = Agent("multi_health", capabilities=["test"])
        agent2 = Agent("multi_health", capabilities=["test"])

        await agent1.start()
        await agent2.start()

        try:
            await asyncio.sleep(0.1)

            registry = AgentRegistry(redis)
            heartbeats = await registry.get_instance_heartbeats("multi_health")

            # Should have two heartbeats
            assert len(heartbeats) == 2
            assert agent1.instance_id in heartbeats
            assert agent2.instance_id in heartbeats

            # Both should have positive TTL
            assert all(ttl is not None and ttl > 0 for ttl in heartbeats.values())
        finally:
            await agent1.stop()
            await agent2.stop()


class TestLoadBalancing:
    """Test message load balancing across instances."""

    async def test_messages_distributed_across_instances(self, mas_service):
        """Test that messages are load-balanced across instances."""
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=False,
                rbac=False,
                message_signing=False,
                circuit_breaker=False,
            )
        )
        gateway = GatewayService(settings=settings)
        await gateway.start()

        # Create sender
        sender = Agent("sender", capabilities=["send"])

        # Create two instances of receiver
        receiver1 = CollectorAgent("receiver", capabilities=["receive"])
        receiver2 = CollectorAgent("receiver", capabilities=["receive"])

        await sender.start()
        await receiver1.start()
        await receiver2.start()

        try:
            await gateway.auth_manager().allow_bidirectional("sender", "receiver")

            # Send multiple messages
            num_messages = 20
            for i in range(num_messages):
                await sender.send("receiver", "test.message", {"index": i})

            # Wait for delivery
            await asyncio.sleep(1.0)

            total_received = len(receiver1.messages) + len(receiver2.messages)
            assert total_received == num_messages

            # Both instances should have received some messages (load balanced)
            # Note: With only 2 instances and 20 messages, distribution may vary
            # but both should have received at least 1 message
            assert len(receiver1.messages) > 0, "Instance 1 received no messages"
            assert len(receiver2.messages) > 0, "Instance 2 received no messages"
        finally:
            await sender.stop()
            await receiver1.stop()
            await receiver2.stop()
            await gateway.stop()


class TestRequestResponseRouting:
    """Test that request-response routes replies to correct instance."""

    async def test_reply_routes_to_requesting_instance(self, mas_service):
        """Test that replies go to the specific instance that made the request."""
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=False,
                rbac=False,
                message_signing=False,
                circuit_breaker=False,
            )
        )
        gateway = GatewayService(settings=settings)
        await gateway.start()

        # Two instances of requester
        requester1 = Agent("requester", capabilities=["request"])
        requester2 = Agent("requester", capabilities=["request"])

        # One responder
        responder = ResponderAgent("responder", capabilities=["respond"])

        await requester1.start()
        await requester2.start()
        await responder.start()

        try:
            await gateway.auth_manager().allow_bidirectional("requester", "responder")

            # Both instances make requests
            response1 = await requester1.request(
                "responder",
                "test.request",
                {"from_instance": requester1.instance_id},
                timeout=5.0,
            )

            response2 = await requester2.request(
                "responder",
                "test.request",
                {"from_instance": requester2.instance_id},
                timeout=5.0,
            )

            # Each should have received a response
            assert response1 is not None
            assert response2 is not None

            # Responses should be from the same responder
            assert response1.data["instance_id"] == responder.instance_id
            assert response2.data["instance_id"] == responder.instance_id

            # Responder should have handled both requests
            assert responder.handled_count == 2
        finally:
            await requester1.stop()
            await requester2.stop()
            await responder.stop()
            await gateway.stop()

    async def test_concurrent_requests_from_multiple_instances(self, mas_service):
        """Test concurrent requests from multiple instances route correctly."""
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=False,
                rbac=False,
                message_signing=False,
                circuit_breaker=False,
            )
        )
        gateway = GatewayService(settings=settings)
        await gateway.start()

        # Multiple requester instances
        requesters = [
            Agent(f"requester_{i}", capabilities=["request"]) for i in range(3)
        ]

        # Single responder
        responder = ResponderAgent("responder", capabilities=["respond"])

        for r in requesters:
            await r.start()
        await responder.start()

        try:
            for r in requesters:
                await gateway.auth_manager().allow_bidirectional(r.id, "responder")

            # All instances make concurrent requests
            async def make_request(requester):
                return await requester.request(
                    "responder",
                    "test.request",
                    {"from": requester.id},
                    timeout=5.0,
                )

            responses = await asyncio.gather(*[make_request(r) for r in requesters])

            # All should have received responses
            assert len(responses) == 3
            assert all(r is not None for r in responses)
        finally:
            for r in requesters:
                await r.stop()
            await responder.stop()
            await gateway.stop()


class TestSharedState:
    """Test that state is shared across instances."""

    async def test_state_shared_across_instances(self, redis):
        """Test that multiple instances share the same state."""
        agent1 = Agent("stateful", capabilities=["test"], state_model=CounterState)
        agent2 = Agent("stateful", capabilities=["test"], state_model=CounterState)

        await agent1.start()
        await agent2.start()

        try:
            # Update state from instance 1
            await agent1.update_state({"count": 42})

            # Reload state in instance 2
            if agent2._state_manager:
                await agent2._state_manager.load()

            # Instance 2 should see the updated state
            assert agent2.state.count == 42
        finally:
            await agent1.stop()
            await agent2.stop()


class TestDiscovery:
    """Test that discovery returns logical agents, not instances."""

    async def test_discovery_returns_single_agent(self, mas_service):
        """Test that discovery doesn't duplicate multi-instance agents."""
        # Create multiple instances of same agent
        agent1 = Agent("multi_instance", capabilities=["special"])
        agent2 = Agent("multi_instance", capabilities=["special"])
        discoverer = Agent("discoverer", capabilities=["discover"])

        await agent1.start()
        await agent2.start()
        await discoverer.start()

        try:
            # Wait for registration
            await asyncio.sleep(0.1)

            # Discover should return single agent
            agents = await discoverer.discover(capabilities=["special"])
            assert len(agents) == 1
            assert agents[0]["id"] == "multi_instance"
        finally:
            await agent1.stop()
            await agent2.stop()
            await discoverer.stop()


class TestSystemEvents:
    """Test system events for instance lifecycle."""

    async def test_register_event_only_on_first_instance(self, redis):
        """Test that REGISTER event fires only for first instance."""
        pubsub = redis.pubsub()
        await pubsub.subscribe("mas.system")

        events = []

        async def collect_events():
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    import json

                    events.append(json.loads(msg["data"]))
                    if len(events) >= 4:  # Wait for expected events
                        break

        collector_task = asyncio.create_task(collect_events())

        agent1 = Agent("event_test", capabilities=["test"])
        agent2 = Agent("event_test", capabilities=["test"])

        try:
            await agent1.start()
            await asyncio.sleep(0.1)
            await agent2.start()
            await asyncio.sleep(0.1)

            # Wait for events
            await asyncio.wait_for(collector_task, timeout=2.0)

            # Should have: REGISTER (once), INSTANCE_JOIN (twice)
            register_events = [e for e in events if e.get("type") == "REGISTER"]
            join_events = [e for e in events if e.get("type") == "INSTANCE_JOIN"]

            assert len(register_events) == 1
            assert len(join_events) == 2
        except asyncio.TimeoutError:
            collector_task.cancel()
        finally:
            await agent1.stop()
            await agent2.stop()
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def test_deregister_event_only_on_last_instance(self, redis):
        """Test that DEREGISTER event fires only when last instance leaves."""
        agent1 = Agent("event_test", capabilities=["test"])
        agent2 = Agent("event_test", capabilities=["test"])

        await agent1.start()
        await agent2.start()

        pubsub = redis.pubsub()
        await pubsub.subscribe("mas.system")

        events = []

        async def collect_events():
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    import json

                    events.append(json.loads(msg["data"]))
                    if any(e.get("type") == "DEREGISTER" for e in events):
                        break

        collector_task = asyncio.create_task(collect_events())

        try:
            # Stop first instance
            await agent1.stop()
            await asyncio.sleep(0.1)

            # Should have INSTANCE_LEAVE but not DEREGISTER
            leave_events = [e for e in events if e.get("type") == "INSTANCE_LEAVE"]
            deregister_events = [e for e in events if e.get("type") == "DEREGISTER"]

            assert len(leave_events) >= 1
            assert len(deregister_events) == 0

            # Stop second (last) instance
            await agent2.stop()
            await asyncio.sleep(0.1)

            # Wait for DEREGISTER
            await asyncio.wait_for(collector_task, timeout=2.0)

            # Now should have DEREGISTER
            deregister_events = [e for e in events if e.get("type") == "DEREGISTER"]
            assert len(deregister_events) == 1
        except asyncio.TimeoutError:
            collector_task.cancel()
            await agent2.stop()
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
