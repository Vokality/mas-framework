"""Regression tests to ensure optimizations don't break functionality.

These tests verify that core behaviors remain correct after optimization.
Run these tests before and after each optimization to ensure no regressions.

Usage:
    uv run pytest tests/test_regression.py -v
"""

import json
import time

import pytest

pytestmark = pytest.mark.asyncio


# -----------------------------------------------------------------------------
# Rate Limit Regression Tests
# -----------------------------------------------------------------------------


class TestRateLimitRegression:
    """Ensure rate limiting behavior is preserved after optimization."""

    async def test_allows_requests_within_limit(self, redis):
        """Requests within the limit must be allowed."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(redis, default_per_minute=10, default_per_hour=100)
        agent_id = "regression_rate_allow"

        for i in range(10):
            result = await module.check_rate_limit(agent_id, f"msg_{i}")
            assert result.allowed, f"Message {i} should be allowed (within limit)"
            assert result.remaining >= 0

    async def test_blocks_requests_over_limit(self, redis):
        """Requests over the limit must be blocked."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(redis, default_per_minute=5, default_per_hour=100)
        agent_id = "regression_rate_block"

        # First 5 should pass
        for i in range(5):
            result = await module.check_rate_limit(agent_id, f"msg_{i}")
            assert result.allowed, f"Message {i} should be allowed"

        # 6th should be blocked
        result = await module.check_rate_limit(agent_id, "msg_blocked")
        assert not result.allowed, "Message over limit should be blocked"
        assert result.remaining == 0

    async def test_custom_limits_applied(self, redis):
        """Custom per-agent limits must be respected."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(redis, default_per_minute=100, default_per_hour=1000)
        agent_id = "regression_rate_custom"

        # Set custom low limit
        await module.set_limits(agent_id, per_minute=3)

        # First 3 should pass
        for i in range(3):
            result = await module.check_rate_limit(agent_id, f"custom_msg_{i}")
            assert result.allowed

        # 4th should be blocked (custom limit)
        result = await module.check_rate_limit(agent_id, "custom_msg_blocked")
        assert not result.allowed

    async def test_different_agents_independent(self, redis):
        """Each agent has independent rate limits."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(redis, default_per_minute=2, default_per_hour=100)

        # Agent A uses its limit
        for i in range(2):
            result = await module.check_rate_limit("agent_a", f"msg_{i}")
            assert result.allowed

        # Agent A is now blocked
        result = await module.check_rate_limit("agent_a", "msg_blocked")
        assert not result.allowed

        # Agent B should still be allowed (independent limit)
        result = await module.check_rate_limit("agent_b", "msg_0")
        assert result.allowed, "Agent B should have independent limit"

    async def test_remaining_count_accurate(self, redis):
        """Remaining count must be accurate."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(redis, default_per_minute=5, default_per_hour=100)
        agent_id = "regression_rate_remaining"

        result = await module.check_rate_limit(agent_id, "msg_0")
        assert result.remaining == 4  # 5 - 1 = 4

        result = await module.check_rate_limit(agent_id, "msg_1")
        assert result.remaining == 3  # 5 - 2 = 3


# -----------------------------------------------------------------------------
# Discovery Regression Tests
# -----------------------------------------------------------------------------


class TestDiscoveryRegression:
    """Ensure discovery behavior is preserved after optimization."""

    @pytest.fixture
    async def setup_discovery_agents(self, redis):
        """Setup agents with various states and capabilities."""
        agents = [
            ("active_nlp", ["nlp", "text"], "ACTIVE"),
            ("active_vision", ["vision", "image"], "ACTIVE"),
            ("active_both", ["nlp", "vision"], "ACTIVE"),
            ("inactive_nlp", ["nlp"], "INACTIVE"),
            ("active_no_caps", [], "ACTIVE"),
        ]

        for agent_id, caps, status in agents:
            await redis.hset(
                f"agent:{agent_id}",
                mapping={
                    "id": agent_id,
                    "capabilities": json.dumps(caps),
                    "metadata": json.dumps({"test": True}),
                    "status": status,
                    "registered_at": str(time.time()),
                },
            )

        yield agents

        # Cleanup
        for agent_id, _, _ in agents:
            await redis.delete(f"agent:{agent_id}")

    async def test_discovers_all_active_agents(self, redis, setup_discovery_agents):
        """Discovery without filter returns all active agents."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        agents = await registry.discover()

        agent_ids = {a["id"] for a in agents}

        # Should find all active agents
        assert "active_nlp" in agent_ids
        assert "active_vision" in agent_ids
        assert "active_both" in agent_ids
        assert "active_no_caps" in agent_ids

        # Should NOT find inactive agents
        assert "inactive_nlp" not in agent_ids

    async def test_discovers_by_capability(self, redis, setup_discovery_agents):
        """Discovery filters by capability correctly."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)

        # Find NLP agents
        nlp_agents = await registry.discover(capabilities=["nlp"])
        nlp_ids = {a["id"] for a in nlp_agents}

        assert "active_nlp" in nlp_ids
        assert "active_both" in nlp_ids
        assert "active_vision" not in nlp_ids
        assert "inactive_nlp" not in nlp_ids  # Inactive excluded

    async def test_discovers_with_multiple_capabilities(
        self, redis, setup_discovery_agents
    ):
        """Discovery with multiple capabilities uses OR logic."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)

        # Find agents with nlp OR vision
        agents = await registry.discover(capabilities=["nlp", "vision"])
        agent_ids = {a["id"] for a in agents}

        assert "active_nlp" in agent_ids
        assert "active_vision" in agent_ids
        assert "active_both" in agent_ids

    async def test_discovery_returns_correct_data(self, redis, setup_discovery_agents):
        """Discovery returns complete and correct agent data."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)

        agents = await registry.discover(capabilities=["nlp"])

        # Find specific agent
        nlp_agent = next(a for a in agents if a["id"] == "active_nlp")

        assert nlp_agent["id"] == "active_nlp"
        assert "nlp" in nlp_agent["capabilities"]
        assert "text" in nlp_agent["capabilities"]
        assert nlp_agent["metadata"]["test"] is True

    async def test_empty_discovery_returns_empty_list(self, redis):
        """Discovery with no matches returns empty list."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)

        agents = await registry.discover(capabilities=["nonexistent_capability"])

        assert agents == []


# -----------------------------------------------------------------------------
# Audit Regression Tests
# -----------------------------------------------------------------------------


class TestAuditRegression:
    """Ensure audit logging behavior is preserved after optimization."""

    async def test_log_message_creates_entry(self, redis):
        """Logging a message creates an audit entry."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        stream_id = await audit.log_message(
            message_id="regression_msg_1",
            sender_id="sender_a",
            target_id="target_b",
            decision="ALLOWED",
            latency_ms=15.5,
            payload={"test": "data"},
            violations=[],
        )

        assert stream_id is not None
        assert isinstance(stream_id, str)

    async def test_query_by_sender(self, redis):
        """Audit entries can be queried by sender."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        # Log messages from different senders
        await audit.log_message("msg_s1_1", "sender_one", "target", "ALLOWED", 10.0, {})
        await audit.log_message("msg_s1_2", "sender_one", "target", "ALLOWED", 10.0, {})
        await audit.log_message("msg_s2_1", "sender_two", "target", "ALLOWED", 10.0, {})

        # Query by sender_one
        entries = await audit.query_by_sender("sender_one")

        assert len(entries) >= 2
        assert all(e["sender_id"] == "sender_one" for e in entries)

    async def test_query_by_target(self, redis):
        """Audit entries can be queried by target."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        await audit.log_message("msg_t1", "sender", "target_one", "ALLOWED", 10.0, {})
        await audit.log_message("msg_t2", "sender", "target_two", "DENIED", 10.0, {})

        entries = await audit.query_by_target("target_one")

        assert len(entries) >= 1
        assert all(e["target_id"] == "target_one" for e in entries)

    async def test_violations_stored_correctly(self, redis):
        """Violations are stored and retrieved correctly."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        await audit.log_message(
            message_id="msg_violations",
            sender_id="violation_sender",
            target_id="target",
            decision="DLP_BLOCKED",
            latency_ms=10.0,
            payload={"sensitive": "data"},
            violations=["ssn", "credit_card"],
        )

        entries = await audit.query_by_sender("violation_sender")
        entry = next(e for e in entries if e["message_id"] == "msg_violations")

        assert "ssn" in entry["violations"]
        assert "credit_card" in entry["violations"]

    async def test_security_events_logged(self, redis):
        """Security events are logged to separate stream."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        stream_id = await audit.log_security_event(
            "AUTH_FAILURE",
            {"agent_id": "bad_agent", "reason": "invalid_token"},
        )

        assert stream_id is not None

        events = await audit.query_security_events()
        assert len(events) >= 1


# -----------------------------------------------------------------------------
# Circuit Breaker Regression Tests
# -----------------------------------------------------------------------------


class TestCircuitBreakerRegression:
    """Ensure circuit breaker behavior is preserved after optimization."""

    async def test_closed_by_default(self, redis):
        """Circuit is closed (allowing traffic) by default."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule, CircuitState

        cb = CircuitBreakerModule(redis)

        status = await cb.check_circuit("new_target")

        assert status.state == CircuitState.CLOSED
        assert status.allowed is True
        assert status.failure_count == 0

    async def test_opens_after_failures(self, redis):
        """Circuit opens after threshold failures."""
        from mas.gateway.circuit_breaker import (
            CircuitBreakerConfig,
            CircuitBreakerModule,
            CircuitState,
        )

        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreakerModule(redis, config=config)
        target = "failing_target"

        # Record failures
        for i in range(3):
            await cb.record_failure(target, f"failure_{i}")

        status = await cb.check_circuit(target)

        assert status.state == CircuitState.OPEN
        assert status.allowed is False

        await cb.reset_circuit(target)

    async def test_success_resets_failure_count(self, redis):
        """Success in closed state resets failure count."""
        from mas.gateway.circuit_breaker import (
            CircuitBreakerConfig,
            CircuitBreakerModule,
        )

        config = CircuitBreakerConfig(failure_threshold=5)
        cb = CircuitBreakerModule(redis, config=config)
        target = "reset_target"

        # Record some failures (not enough to trip)
        await cb.record_failure(target, "fail_1")
        await cb.record_failure(target, "fail_2")

        status = await cb.check_circuit(target)
        assert status.failure_count == 2

        # Success should reset
        await cb.record_success(target)

        status = await cb.check_circuit(target)
        assert status.failure_count == 0

        await cb.reset_circuit(target)

    async def test_half_open_closes_on_success(self, redis):
        """Circuit closes from half-open after success threshold.

        Note: This test directly manipulates circuit state to avoid
        timing-sensitive transitions that can be flaky in CI.
        """
        from mas.gateway.circuit_breaker import (
            CircuitBreakerConfig,
            CircuitBreakerModule,
            CircuitState,
        )

        config = CircuitBreakerConfig(
            failure_threshold=2,
            success_threshold=2,
            timeout_seconds=60.0,  # Long timeout - we'll manipulate state directly
            window_seconds=120.0,
        )
        cb = CircuitBreakerModule(redis, config=config)
        target = "halfopen_regression_test"

        try:
            # Directly set circuit to HALF_OPEN state for testing
            # This avoids timing-sensitive failure threshold + timeout transitions
            await redis.hset(
                f"circuit:{target}",
                mapping={
                    "state": CircuitState.HALF_OPEN.value,
                    "failure_count": "2",
                    "success_count": "0",
                    "last_failure_time": "0",
                    "opened_at": "0",
                },
            )

            # Verify we're in HALF_OPEN
            status = await cb.check_circuit(target)
            assert status.state == CircuitState.HALF_OPEN, (
                f"Circuit should be HALF_OPEN, got {status.state}"
            )
            assert status.allowed is True, "HALF_OPEN should allow traffic"

            # Record first success
            status1 = await cb.record_success(target)
            assert status1.success_count == 1, "Should have 1 success"
            assert status1.state == CircuitState.HALF_OPEN, (
                "Should still be HALF_OPEN after 1 success"
            )

            # Record second success - should close circuit
            status2 = await cb.record_success(target)
            assert status2.state == CircuitState.CLOSED, (
                f"Circuit should be CLOSED after 2 successes, got {status2.state}"
            )
            assert status2.failure_count == 0, "Failure count should reset"
            assert status2.success_count == 0, "Success count should reset"

        finally:
            await cb.reset_circuit(target)


# -----------------------------------------------------------------------------
# Registry Regression Tests
# -----------------------------------------------------------------------------


class TestRegistryRegression:
    """Ensure registry behavior is preserved after optimization."""

    async def test_register_creates_agent(self, redis):
        """Registration creates agent entry with correct data."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        instance_id = "test1234"

        token = await registry.register(
            "regression_agent",
            instance_id,
            capabilities=["cap1", "cap2"],
            metadata={"key": "value"},
        )

        assert token is not None
        assert len(token) > 0

        agent = await registry.get_agent("regression_agent")
        assert agent is not None
        assert agent["id"] == "regression_agent"
        assert "cap1" in agent["capabilities"]
        assert "cap2" in agent["capabilities"]

        await registry.deregister("regression_agent", instance_id)

    async def test_deregister_removes_agent(self, redis):
        """Deregistration removes agent entry (when last instance leaves)."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        instance_id = "test1234"

        await registry.register("dereg_agent", instance_id, capabilities=[])

        # Verify exists
        agent = await registry.get_agent("dereg_agent")
        assert agent is not None

        # Deregister (last instance)
        await registry.deregister("dereg_agent", instance_id)

        # Verify removed
        agent = await registry.get_agent("dereg_agent")
        assert agent is None

    async def test_deregister_preserves_state_by_default(self, redis):
        """Deregistration preserves state by default."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        instance_id = "test1234"

        await registry.register("state_agent", instance_id, capabilities=[])

        # Create some state
        await redis.hset("agent.state:state_agent", mapping={"data": "preserved"})

        # Deregister with default (keep_state=True)
        await registry.deregister("state_agent", instance_id)

        # State should still exist
        state = await redis.hgetall("agent.state:state_agent")
        assert state.get("data") == "preserved"

        # Cleanup
        await redis.delete("agent.state:state_agent")

    async def test_deregister_can_remove_state(self, redis):
        """Deregistration can remove state when requested."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        instance_id = "test1234"

        await registry.register("state_remove_agent", instance_id, capabilities=[])
        await redis.hset("agent.state:state_remove_agent", mapping={"data": "remove"})

        # Deregister with keep_state=False
        await registry.deregister("state_remove_agent", instance_id, keep_state=False)

        # State should be removed
        state = await redis.hgetall("agent.state:state_remove_agent")
        assert state == {}

    async def test_heartbeat_updates(self, redis):
        """Heartbeat creates/updates heartbeat key."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        instance_id = "test1234"

        await registry.register("heartbeat_agent", instance_id, capabilities=[])

        # Update heartbeat (now requires instance_id)
        await registry.update_heartbeat("heartbeat_agent", instance_id, ttl=60)

        # Verify heartbeat key exists (new format includes instance_id)
        ttl = await redis.ttl(f"agent:heartbeat_agent:heartbeat:{instance_id}")
        assert ttl > 0
        assert ttl <= 60

        await registry.deregister("heartbeat_agent", instance_id)


# -----------------------------------------------------------------------------
# State Manager Regression Tests
# -----------------------------------------------------------------------------


class TestStateManagerRegression:
    """Ensure state management behavior is preserved after optimization."""

    async def test_state_persists_and_loads(self, redis):
        """State is persisted and can be loaded."""
        from mas.state import StateManager

        # Create and save state
        manager1 = StateManager("state_test_agent", redis)
        await manager1.load()
        await manager1.update({"counter": 42, "name": "test"})

        # Create new manager and load
        manager2 = StateManager("state_test_agent", redis)
        await manager2.load()

        # Values should be preserved (as strings from Redis)
        assert manager2.state["counter"] == "42"
        assert manager2.state["name"] == "test"

        # Cleanup
        await redis.delete("agent.state:state_test_agent")

    async def test_state_reset(self, redis):
        """State reset clears data."""
        from mas.state import StateManager

        manager = StateManager("reset_agent", redis)
        await manager.load()
        await manager.update({"data": "value"})

        # Reset
        await manager.reset()

        # State should be empty
        assert manager.state == {}

        # Redis key should be deleted
        exists = await redis.exists("agent.state:reset_agent")
        assert exists == 0

    async def test_complex_values_serialized(self, redis):
        """Complex values (dict, list) are JSON serialized."""
        from mas.state import StateManager

        manager = StateManager("complex_state_agent", redis)
        await manager.load()
        await manager.update(
            {
                "nested": {"a": 1, "b": 2},
                "items": [1, 2, 3],
            }
        )

        # Load in new manager
        manager2 = StateManager("complex_state_agent", redis)
        await manager2.load()

        # Values are stored as JSON strings
        assert manager2.state["nested"] == '{"a": 1, "b": 2}'
        assert manager2.state["items"] == "[1, 2, 3]"

        # Cleanup
        await redis.delete("agent.state:complex_state_agent")
