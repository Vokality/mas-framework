"""Circuit Breaker Module - Detect and handle target agent failures."""

import logging
import time
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from pydantic import BaseModel

from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests pass through
    OPEN = "open"  # Circuit tripped, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration."""

    failure_threshold: int = 5  # Failures before opening circuit
    success_threshold: int = 2  # Successes to close from half-open
    timeout_seconds: float = 60.0  # Time before trying half-open
    window_seconds: float = 60.0  # Rolling window for failure counting


class CircuitStatus(BaseModel):
    """Current circuit breaker status."""

    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: Optional[float] = None
    opened_at: Optional[float] = None
    allowed: bool  # Whether message is allowed through


class CircuitBreakerModule:
    """
    Circuit Breaker module for handling target agent failures.

    Implements the circuit breaker pattern as per GATEWAY.md:
    - Monitor target agent health (response rate, error rate)
    - Open circuit after N failures (stop forwarding messages)
    - Half-open state (trial messages to test recovery)
    - Auto-recovery after timeout
    - Dead Letter Queue (DLQ) for failed messages

    States:
    - CLOSED: Normal operation, all messages pass through
    - OPEN: Circuit tripped, messages are blocked/queued
    - HALF_OPEN: Testing recovery, limited messages pass through

    Usage:
        breaker = CircuitBreakerModule(redis)
        status = await breaker.check_circuit(target_id)
        if status.allowed:
            # Send message
            await breaker.record_success(target_id)
        else:
            # Route to DLQ
            await breaker.record_failure(target_id, reason)
    """

    def __init__(
        self,
        redis: AsyncRedisProtocol,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        """
        Initialize circuit breaker module.

        Args:
            redis: Redis connection
            config: Circuit breaker configuration
        """
        self.redis: AsyncRedisProtocol = redis
        self.config = config or CircuitBreakerConfig()

    def _parse_circuit_data(
        self, circuit_data: Mapping[str, str]
    ) -> Tuple[CircuitState, int, int, Optional[float], Optional[float]]:
        """Parse Redis circuit data into typed values."""
        state = CircuitState(circuit_data.get("state", CircuitState.CLOSED.value))
        failure_count = int(circuit_data.get("failure_count", 0))
        success_count = int(circuit_data.get("success_count", 0))
        last_failure_time_raw = circuit_data.get("last_failure_time")
        opened_at_raw = circuit_data.get("opened_at")

        last_failure_time = (
            float(last_failure_time_raw) if last_failure_time_raw else None
        )
        opened_at = float(opened_at_raw) if opened_at_raw else None
        return state, failure_count, success_count, last_failure_time, opened_at

    def _maybe_transition_half_open(
        self,
        state: CircuitState,
        opened_at: Optional[float],
        success_count: int,
        current_time: float,
    ) -> Tuple[CircuitState, int, bool]:
        """Transition OPEN to HALF_OPEN when timeout expires."""
        if state == CircuitState.OPEN and opened_at:
            if (current_time - opened_at) >= self.config.timeout_seconds:
                return CircuitState.HALF_OPEN, 0, True
        return state, success_count, False

    def _next_failure_count(
        self,
        *,
        has_data: bool,
        last_failure_time: Optional[float],
        current_time: float,
        failure_count: int,
    ) -> int:
        """Compute the next failure count based on windowing."""
        if not has_data:
            return 1
        if last_failure_time and (
            current_time - last_failure_time > self.config.window_seconds
        ):
            return 1
        return failure_count + 1

    @staticmethod
    def _default_status() -> CircuitStatus:
        """Return default closed circuit status."""
        return CircuitStatus(
            state=CircuitState.CLOSED,
            failure_count=0,
            success_count=0,
            allowed=True,
        )

    async def check_circuit(self, target_id: str) -> CircuitStatus:
        """
        Check circuit breaker state for target agent.

        Args:
            target_id: Target agent ID

        Returns:
            Circuit status with state and whether message is allowed
        """
        circuit_key = f"circuit:{target_id}"
        circuit_data = self._normalize_hash(await self.redis.hgetall(circuit_key))

        if not circuit_data:
            # No circuit data, default to CLOSED
            return self._default_status()

        state, failure_count, success_count, last_failure_time, opened_at = (
            self._parse_circuit_data(circuit_data)
        )

        current_time = time.time()

        # State transitions
        state, success_count, transitioned = self._maybe_transition_half_open(
            state, opened_at, success_count, current_time
        )
        if transitioned:
            await self._update_state(target_id, state, failure_count, success_count)
            logger.info(
                f"Circuit breaker HALF_OPEN for {target_id} after timeout",
                extra={"target_id": target_id, "state": state},
            )

        # Determine if message is allowed
        allowed = state == CircuitState.CLOSED or state == CircuitState.HALF_OPEN

        return CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=last_failure_time,
            opened_at=opened_at,
            allowed=allowed,
        )

    async def record_success(self, target_id: str) -> CircuitStatus:
        """
        Record successful message delivery to target.

        Args:
            target_id: Target agent ID

        Returns:
            Updated circuit status
        """
        circuit_key = f"circuit:{target_id}"
        circuit_data = self._normalize_hash(await self.redis.hgetall(circuit_key))

        if not circuit_data:
            # No circuit data, initialize
            return self._default_status()

        state, failure_count, success_count, _last_failure_time, _opened_at = (
            self._parse_circuit_data(circuit_data)
        )

        if state == CircuitState.HALF_OPEN:
            success_count += 1

            # Check if we've had enough successes to close circuit
            if success_count >= self.config.success_threshold:
                state = CircuitState.CLOSED
                failure_count = 0
                success_count = 0
                logger.info(
                    f"Circuit breaker CLOSED for {target_id} after {self.config.success_threshold} successes",
                    extra={"target_id": target_id, "state": state},
                )

            await self._update_state(target_id, state, failure_count, success_count)

        elif state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            if failure_count > 0:
                failure_count = 0
                await self._update_state(target_id, state, failure_count, success_count)

        return CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            allowed=state != CircuitState.OPEN,
        )

    async def check_and_record_success(
        self, target_id: str
    ) -> Tuple[CircuitStatus, CircuitStatus]:
        """
        Check circuit and record success in one operation.

        This method combines check_circuit and record_success to avoid
        the double-fetch pattern where both methods read the same data.
        Uses a single hgetall call instead of two.

        Args:
            target_id: Target agent ID

        Returns:
            Tuple of (check_status, record_status)
        """
        circuit_key = f"circuit:{target_id}"
        circuit_data = self._normalize_hash(await self.redis.hgetall(circuit_key))

        if not circuit_data:
            # No circuit data, default to CLOSED - no need to record
            status = self._default_status()
            return status, status

        state, failure_count, success_count, last_failure_time, opened_at = (
            self._parse_circuit_data(circuit_data)
        )

        current_time = time.time()

        # State transitions for check
        state, success_count, _transitioned = self._maybe_transition_half_open(
            state, opened_at, success_count, current_time
        )

        allowed = state == CircuitState.CLOSED or state == CircuitState.HALF_OPEN

        check_status = CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=last_failure_time,
            opened_at=opened_at,
            allowed=allowed,
        )

        # Now record success if allowed
        if not allowed:
            return check_status, check_status

        if state == CircuitState.HALF_OPEN:
            success_count += 1
            if success_count >= self.config.success_threshold:
                state = CircuitState.CLOSED
                failure_count = 0
                success_count = 0
            await self._update_state(target_id, state, failure_count, success_count)
        elif state == CircuitState.CLOSED and failure_count > 0:
            failure_count = 0
            await self._update_state(target_id, state, failure_count, success_count)

        record_status = CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            allowed=state != CircuitState.OPEN,
        )

        return check_status, record_status

    async def check_and_record_failure(
        self, target_id: str, reason: str = "unknown"
    ) -> Tuple[CircuitStatus, CircuitStatus]:
        """
        Check circuit and record failure in one operation.

        This method combines check_circuit and record_failure to avoid
        the double-fetch pattern where both methods read the same data.
        Uses a single hgetall call instead of two.

        Args:
            target_id: Target agent ID
            reason: Failure reason

        Returns:
            Tuple of (check_status, record_status)
        """
        circuit_key = f"circuit:{target_id}"
        circuit_data = self._normalize_hash(await self.redis.hgetall(circuit_key))

        current_time = time.time()
        opened_at: Optional[float] = None
        has_data = bool(circuit_data)

        if not circuit_data:
            state = CircuitState.CLOSED
            failure_count = 1
            success_count = 0
            last_failure_time = None
        else:
            state, failure_count, success_count, last_failure_time, opened_at = (
                self._parse_circuit_data(circuit_data)
            )

            # State transition for check (OPEN -> HALF_OPEN after timeout)
            state, success_count, _transitioned = self._maybe_transition_half_open(
                state, opened_at, success_count, current_time
            )

        allowed = state == CircuitState.CLOSED or state == CircuitState.HALF_OPEN

        check_status = CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=last_failure_time,
            opened_at=opened_at,
            allowed=allowed,
        )

        # Now record failure
        failure_count = self._next_failure_count(
            has_data=has_data,
            last_failure_time=last_failure_time,
            current_time=current_time,
            failure_count=failure_count,
        )

        # Check if we should open circuit
        if (
            state == CircuitState.CLOSED
            and failure_count >= self.config.failure_threshold
        ):
            state = CircuitState.OPEN
            opened_at = current_time
            logger.warning(
                f"Circuit breaker OPEN for {target_id} after {failure_count} failures",
                extra={
                    "target_id": target_id,
                    "state": state,
                    "failure_count": failure_count,
                    "reason": reason,
                },
            )
        elif state == CircuitState.HALF_OPEN:
            state = CircuitState.OPEN
            opened_at = current_time
            logger.warning(
                f"Circuit breaker back to OPEN for {target_id} (half-open test failed)",
                extra={"target_id": target_id, "state": state, "reason": reason},
            )

        # Update circuit state
        await self.redis.hset(
            circuit_key,
            mapping={
                "state": state.value,
                "failure_count": str(failure_count),
                "success_count": str(success_count),
                "last_failure_time": str(current_time),
                "opened_at": str(opened_at) if opened_at else "",
            },
        )
        await self.redis.expire(circuit_key, int(self.config.timeout_seconds * 2))

        record_status = CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=current_time,
            opened_at=opened_at,
            allowed=state != CircuitState.OPEN,
        )

        return check_status, record_status

    async def record_failure(
        self, target_id: str, reason: str = "unknown"
    ) -> CircuitStatus:
        """
        Record failed message delivery to target.

        Args:
            target_id: Target agent ID
            reason: Failure reason

        Returns:
            Updated circuit status
        """
        circuit_key = f"circuit:{target_id}"
        circuit_data = self._normalize_hash(await self.redis.hgetall(circuit_key))

        current_time = time.time()
        opened_at: Optional[float] = None
        has_data = bool(circuit_data)

        if not circuit_data:
            # Initialize circuit data
            state = CircuitState.CLOSED
            failure_count = 1
            success_count = 0
            last_failure_time = None
        else:
            state, failure_count, success_count, last_failure_time, opened_at = (
                self._parse_circuit_data(circuit_data)
            )

        # Check if failures are within window
        failure_count = self._next_failure_count(
            has_data=has_data,
            last_failure_time=last_failure_time,
            current_time=current_time,
            failure_count=failure_count,
        )

        # Check if we should open circuit
        if (
            state == CircuitState.CLOSED
            and failure_count >= self.config.failure_threshold
        ):
            state = CircuitState.OPEN
            opened_at = current_time
            logger.warning(
                f"Circuit breaker OPEN for {target_id} after {failure_count} failures",
                extra={
                    "target_id": target_id,
                    "state": state,
                    "failure_count": failure_count,
                    "reason": reason,
                },
            )
        elif state == CircuitState.HALF_OPEN:
            # Failure in half-open means back to open
            state = CircuitState.OPEN
            opened_at = current_time
            logger.warning(
                f"Circuit breaker back to OPEN for {target_id} (half-open test failed)",
                extra={"target_id": target_id, "state": state, "reason": reason},
            )
        else:
            opened_at = opened_at if has_data else None

        # Update circuit state
        await self.redis.hset(
            circuit_key,
            mapping={
                "state": state.value,
                "failure_count": str(failure_count),
                "success_count": str(success_count),
                "last_failure_time": str(current_time),
                "opened_at": str(opened_at) if opened_at else "",
            },
        )

        # Set TTL to cleanup old circuits
        await self.redis.expire(circuit_key, int(self.config.timeout_seconds * 2))

        return CircuitStatus(
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=current_time,
            opened_at=opened_at,
            allowed=state != CircuitState.OPEN,
        )

    async def _update_state(
        self,
        target_id: str,
        state: CircuitState,
        failure_count: int,
        success_count: int,
    ) -> None:
        """
        Update circuit breaker state in Redis.

        Args:
            target_id: Target agent ID
            state: New state
            failure_count: Current failure count
            success_count: Current success count
        """
        circuit_key = f"circuit:{target_id}"
        await self.redis.hset(
            circuit_key,
            mapping={
                "state": state.value,
                "failure_count": str(failure_count),
                "success_count": str(success_count),
            },
        )

        # Set TTL
        await self.redis.expire(circuit_key, int(self.config.timeout_seconds * 2))

    async def reset_circuit(self, target_id: str) -> None:
        """
        Manually reset circuit breaker to closed state.

        Args:
            target_id: Target agent ID
        """
        circuit_key = f"circuit:{target_id}"
        await self.redis.delete(circuit_key)
        logger.info(
            f"Circuit breaker reset for {target_id}",
            extra={"target_id": target_id},
        )

    async def get_all_circuits(self) -> dict[str, CircuitStatus]:
        """
        Get status of all circuit breakers.

        Returns:
            Dictionary mapping target_id to circuit status
        """
        circuits: dict[str, CircuitStatus] = {}
        pattern = "circuit:*"

        async for key in self.redis.scan_iter(match=pattern):
            target_id = key.replace("circuit:", "")
            status = await self.check_circuit(target_id)
            circuits[target_id] = status

        return circuits

    async def add_to_dlq(
        self, target_id: str, message_id: str, payload: dict[str, Any], reason: str
    ) -> None:
        """
        Add failed message to Dead Letter Queue.

        Args:
            target_id: Target agent ID
            message_id: Message ID
            payload: Message payload
            reason: Failure reason
        """
        dlq_key = "dlq:messages"

        await self.redis.xadd(
            dlq_key,
            {
                "message_id": message_id,
                "target_id": target_id,
                "payload": str(payload),
                "reason": reason,
                "timestamp": str(time.time()),
            },
        )

        logger.info(
            f"Message {message_id} added to DLQ",
            extra={
                "message_id": message_id,
                "target_id": target_id,
                "reason": reason,
            },
        )

    async def get_dlq_messages(self, count: int = 100) -> list[dict[str, Any]]:
        """
        Get messages from Dead Letter Queue.

        Args:
            count: Maximum number of messages to retrieve

        Returns:
            List of DLQ messages
        """
        dlq_key = "dlq:messages"
        messages = await self.redis.xrange(dlq_key, "-", "+", count=count)

        result: list[dict[str, Any]] = []
        for msg_id, msg_data in messages:
            normalized = {str(k): str(v) for k, v in msg_data.items()}
            timestamp_value = normalized.get("timestamp")
            timestamp = float(timestamp_value) if timestamp_value else 0.0
            result.append(
                {
                    "id": msg_id,
                    "message_id": normalized.get("message_id"),
                    "target_id": normalized.get("target_id"),
                    "reason": normalized.get("reason"),
                    "timestamp": timestamp,
                }
            )

        return result

    @staticmethod
    def _normalize_hash(raw: Mapping[str, str] | None) -> dict[str, str]:
        """Normalize Redis hash responses to a plain dict."""
        if not raw:
            return {}
        return dict(raw)
