"""Tests for Circuit Breaker Module."""

import asyncio
import pytest

from mas.gateway.circuit_breaker import (
    CircuitBreakerModule,
    CircuitState,
    CircuitBreakerConfig,
)

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
def breaker(redis):
    """Circuit breaker module fixture."""
    config = CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        timeout_seconds=2.0,
        window_seconds=10.0,
    )
    return CircuitBreakerModule(redis, config)


class TestCircuitBreakerModule:
    """Test suite for circuit breaker."""

    async def test_initial_state_closed(self, breaker):
        """Test that circuit starts in CLOSED state."""
        status = await breaker.check_circuit("test_target")

        assert status.state == CircuitState.CLOSED
        assert status.allowed is True
        assert status.failure_count == 0

    async def test_record_success_in_closed_state(self, breaker):
        """Test recording success in CLOSED state."""
        target_id = "test_target"

        status = await breaker.record_success(target_id)

        assert status.state == CircuitState.CLOSED
        assert status.allowed is True

    async def test_circuit_opens_after_failures(self, breaker):
        """Test circuit opens after threshold failures."""
        target_id = "test_target"

        # Record failures
        status = None
        for i in range(3):
            status = await breaker.record_failure(target_id, f"failure_{i}")

        assert status is not None
        assert status.state == CircuitState.OPEN
        assert status.allowed is False
        assert status.failure_count == 3

    async def test_circuit_stays_closed_below_threshold(self, breaker):
        """Test circuit stays closed below failure threshold."""
        target_id = "test_target"

        # Record failures below threshold
        status = None
        for i in range(2):
            status = await breaker.record_failure(target_id, f"failure_{i}")

        assert status is not None
        assert status.state == CircuitState.CLOSED
        assert status.allowed is True
        assert status.failure_count == 2

    async def test_circuit_opens_blocks_requests(self, breaker):
        """Test open circuit blocks requests."""
        target_id = "test_target"

        # Open circuit
        for i in range(3):
            await breaker.record_failure(target_id, f"failure_{i}")

        # Check status
        status = await breaker.check_circuit(target_id)

        assert status.state == CircuitState.OPEN
        assert status.allowed is False

    async def test_circuit_half_open_after_timeout(self, breaker):
        """Test circuit transitions to HALF_OPEN after timeout."""
        target_id = "test_target"

        # Open circuit
        for i in range(3):
            await breaker.record_failure(target_id, f"failure_{i}")

        # Wait for timeout
        await asyncio.sleep(2.1)

        # Check status should be HALF_OPEN
        status = await breaker.check_circuit(target_id)

        assert status.state == CircuitState.HALF_OPEN
        assert status.allowed is True

    async def test_half_open_closes_after_successes(self, breaker):
        """Test HALF_OPEN closes after success threshold."""
        target_id = "test_target"

        # Open circuit
        for i in range(3):
            await breaker.record_failure(target_id, f"failure_{i}")

        # Wait for timeout
        await asyncio.sleep(2.1)

        # Check to transition to HALF_OPEN
        await breaker.check_circuit(target_id)

        # Record successes
        status = await breaker.record_success(target_id)
        assert status.state == CircuitState.HALF_OPEN

        status = await breaker.record_success(target_id)
        assert status.state == CircuitState.CLOSED
        assert status.allowed is True

    async def test_half_open_reopens_on_failure(self, breaker):
        """Test HALF_OPEN reopens on failure."""
        target_id = "test_target"

        # Open circuit
        for i in range(3):
            await breaker.record_failure(target_id, f"failure_{i}")

        # Wait for timeout
        await asyncio.sleep(2.1)

        # Check to transition to HALF_OPEN
        await breaker.check_circuit(target_id)

        # Record failure
        status = await breaker.record_failure(target_id, "half_open_failure")

        assert status.state == CircuitState.OPEN
        assert status.allowed is False

    async def test_failure_window_resets_count(self, breaker):
        """Test failure count resets outside window."""
        target_id = "test_target"
        config = CircuitBreakerConfig(
            failure_threshold=3,
            window_seconds=1.0,  # Short window
        )
        breaker_short = CircuitBreakerModule(breaker.redis, config)

        # Record 2 failures
        await breaker_short.record_failure(target_id, "failure_1")
        await breaker_short.record_failure(target_id, "failure_2")

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Record another failure (should reset count)
        status = await breaker_short.record_failure(target_id, "failure_3")

        # Circuit should still be closed (count reset)
        assert status.state == CircuitState.CLOSED

    async def test_reset_circuit(self, breaker):
        """Test manually resetting circuit."""
        target_id = "test_target"

        # Open circuit
        for i in range(3):
            await breaker.record_failure(target_id, f"failure_{i}")

        status = await breaker.check_circuit(target_id)
        assert status.state == CircuitState.OPEN

        # Reset
        await breaker.reset_circuit(target_id)

        # Check status
        status = await breaker.check_circuit(target_id)
        assert status.state == CircuitState.CLOSED
        assert status.allowed is True

    async def test_get_all_circuits(self, breaker):
        """Test getting all circuit statuses."""
        # Create multiple circuits
        await breaker.record_failure("target_1", "failure")
        await breaker.record_failure("target_2", "failure")
        await breaker.record_failure("target_2", "failure")

        circuits = await breaker.get_all_circuits()

        assert len(circuits) == 2
        assert "target_1" in circuits
        assert "target_2" in circuits
        assert circuits["target_1"].failure_count == 1
        assert circuits["target_2"].failure_count == 2

    async def test_add_to_dlq(self, breaker):
        """Test adding message to DLQ."""
        target_id = "test_target"
        message_id = "msg_123"
        payload = {"data": "test"}
        reason = "circuit_open"

        await breaker.add_to_dlq(target_id, message_id, payload, reason)

        # Check DLQ
        messages = await breaker.get_dlq_messages()
        assert len(messages) == 1
        assert messages[0]["message_id"] == message_id
        assert messages[0]["target_id"] == target_id
        assert messages[0]["reason"] == reason

    async def test_get_dlq_messages(self, breaker):
        """Test retrieving DLQ messages."""
        # Add multiple messages
        for i in range(5):
            await breaker.add_to_dlq(
                f"target_{i}", f"msg_{i}", {"data": i}, "test_reason"
            )

        messages = await breaker.get_dlq_messages(count=3)

        assert len(messages) == 3

    async def test_success_resets_failure_count_in_closed(self, breaker):
        """Test success resets failure count in CLOSED state."""
        target_id = "test_target"

        # Record some failures
        await breaker.record_failure(target_id, "failure_1")
        await breaker.record_failure(target_id, "failure_2")

        status = await breaker.check_circuit(target_id)
        assert status.failure_count == 2

        # Record success
        await breaker.record_success(target_id)

        # Check failure count reset
        status = await breaker.check_circuit(target_id)
        assert status.failure_count == 0

    async def test_multiple_targets_independent(self, breaker):
        """Test that different targets have independent circuits."""
        # Open circuit for target_1
        for i in range(3):
            await breaker.record_failure("target_1", f"failure_{i}")

        # target_2 should still be closed
        status_1 = await breaker.check_circuit("target_1")
        status_2 = await breaker.check_circuit("target_2")

        assert status_1.state == CircuitState.OPEN
        assert status_2.state == CircuitState.CLOSED
