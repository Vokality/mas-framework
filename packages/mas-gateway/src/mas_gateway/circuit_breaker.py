"""Circuit breaker state transitions coordinated across broker instances."""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Literal, TypedDict

from mas_core import JsonObject
from pydantic import BaseModel, Field, TypeAdapter
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError, WatchError

logger = logging.getLogger(__name__)
_HASH_ADAPTER = TypeAdapter(dict[str, str])
_STREAM_ADAPTER = TypeAdapter(list[tuple[str, dict[str, str]]])
_STRING_ADAPTER = TypeAdapter(str)


class DLQMessage(TypedDict):
    """Message read from the circuit breaker DLQ."""

    id: str
    message_id: str | None
    target_id: str | None
    reason: str | None
    timestamp: float


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig(BaseModel):
    """Positive thresholds and finite timeout/window durations."""

    failure_threshold: int = Field(default=5, ge=1)
    success_threshold: int = Field(default=2, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    window_seconds: float = Field(default=300.0, gt=0, allow_inf_nan=False)


class CircuitStatus(BaseModel):
    """Validated circuit state and its admission decision."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    last_failure_time: float | None = Field(default=None, allow_inf_nan=False)
    opened_at: float | None = Field(default=None, allow_inf_nan=False)
    allowed: bool = True


class CircuitBreakerModule:
    """Persist circuit transitions with optimistic locking across instances."""

    def __init__(
        self,
        redis: Redis,
        config: CircuitBreakerConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis
        self.config = config
        self._clock = clock

    @staticmethod
    def _status_from_data(raw: Mapping[str, str]) -> CircuitStatus:
        """Validate persisted values before applying circuit rules."""
        fields: dict[str, object] = dict(raw)
        for name in ("last_failure_time", "opened_at"):
            if fields.get(name) == "":
                fields[name] = None
        status = CircuitStatus.model_validate(fields)
        status.allowed = status.state != CircuitState.OPEN
        return status

    async def check_circuit(self, target_id: str) -> CircuitStatus:
        """Check admission, persisting timeout transitions when necessary."""
        checked, _ = await self._apply_event(target_id, "check")
        return checked

    def queue_check(self, pipeline: Pipeline, target_id: str) -> None:
        """Queue a read-only admission snapshot on an unwatched pipeline."""
        if pipeline.watching:
            raise ValueError("admission checks require an unwatched pipeline")
        pipeline.hgetall(f"circuit:{target_id}")

    async def resolve_check(self, raw: object, target_id: str) -> CircuitStatus:
        """Validate a queued read and recheck current state before a transition."""
        if isinstance(raw, RedisError):
            raise raw
        original = self._status_from_data(_HASH_ADAPTER.validate_python(raw))
        checked, recorded = self._transition(original, "check")
        if recorded == original:
            return checked
        return await self.check_circuit(target_id)

    async def record_success(self, target_id: str) -> CircuitStatus:
        """Record a successful delivery without losing concurrent events."""
        _, recorded = await self._apply_event(target_id, "success")
        return recorded

    async def record_failure(
        self, target_id: str, reason: str = "unknown"
    ) -> CircuitStatus:
        """Record a failed delivery without losing concurrent events."""
        _, recorded = await self._apply_event(target_id, "failure", reason)
        return recorded

    async def check_and_record_success(
        self, target_id: str
    ) -> tuple[CircuitStatus, CircuitStatus]:
        """Return the admission and successful-delivery states atomically."""
        return await self._apply_event(target_id, "success")

    async def check_and_record_failure(
        self, target_id: str, reason: str = "unknown"
    ) -> tuple[CircuitStatus, CircuitStatus]:
        """Return the admission and failed-delivery states atomically."""
        return await self._apply_event(target_id, "failure", reason)

    def _transition(
        self,
        original: CircuitStatus,
        event: Literal["check", "success", "failure"],
    ) -> tuple[CircuitStatus, CircuitStatus]:
        """Apply the configured rules to a validated state snapshot."""
        checked = original.model_copy()
        now = self._clock()
        if (
            checked.state == CircuitState.OPEN
            and checked.opened_at is not None
            and now - checked.opened_at >= self.config.timeout_seconds
        ):
            checked.state = CircuitState.HALF_OPEN
            checked.success_count = 0
            checked.allowed = True

        recorded = checked.model_copy()
        if event == "success" and recorded.allowed:
            if recorded.state == CircuitState.HALF_OPEN:
                recorded.success_count += 1
                if recorded.success_count >= self.config.success_threshold:
                    recorded.state = CircuitState.CLOSED
                    recorded.failure_count = 0
                    recorded.success_count = 0
                    recorded.last_failure_time = None
                    recorded.opened_at = None
            elif recorded.failure_count:
                recorded.failure_count = 0
                recorded.last_failure_time = None
        elif event == "failure":
            if (
                recorded.last_failure_time is None
                or now - recorded.last_failure_time > self.config.window_seconds
            ):
                recorded.failure_count = 0
            recorded.failure_count += 1
            recorded.last_failure_time = now
            if recorded.state == CircuitState.HALF_OPEN or (
                recorded.state == CircuitState.CLOSED
                and recorded.failure_count >= self.config.failure_threshold
            ):
                recorded.state = CircuitState.OPEN
                recorded.opened_at = now
                recorded.success_count = 0
        recorded.allowed = recorded.state != CircuitState.OPEN
        return checked, recorded

    async def _apply_event(
        self,
        target_id: str,
        event: Literal["check", "success", "failure"],
        reason: str = "unknown",
    ) -> tuple[CircuitStatus, CircuitStatus]:
        """Observe unchanged states once; serialize mutations across brokers."""
        key = f"circuit:{target_id}"
        raw = await self.redis.hgetall(key)
        original = self._status_from_data(_HASH_ADAPTER.validate_python(raw))
        checked, recorded = self._transition(original, event)
        if recorded == original:
            return checked, recorded

        while True:
            async with self.redis.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.hgetall(key)
                    original = self._status_from_data(
                        _HASH_ADAPTER.validate_python(raw)
                    )
                    checked, recorded = self._transition(original, event)

                    if recorded == original:
                        return checked, recorded

                    pipe.multi()
                    pipe.hset(
                        key,
                        mapping={
                            "state": recorded.state.value,
                            "failure_count": str(recorded.failure_count),
                            "success_count": str(recorded.success_count),
                            "last_failure_time": (
                                str(recorded.last_failure_time)
                                if recorded.last_failure_time is not None
                                else ""
                            ),
                            "opened_at": (
                                str(recorded.opened_at)
                                if recorded.opened_at is not None
                                else ""
                            ),
                        },
                    )
                    pipe.expire(
                        key,
                        math.ceil(
                            max(
                                self.config.window_seconds,
                                self.config.timeout_seconds * 2,
                            )
                        ),
                    )
                    await pipe.execute()
                    if recorded.state != original.state:
                        logger.info(
                            "Circuit breaker changed from %s to %s",
                            original.state,
                            recorded.state,
                            extra={"target_id": target_id, "reason": reason},
                        )
                    return checked, recorded
                except WatchError:
                    continue

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

    async def get_all_circuits(
        self, *, limit: int | None = None
    ) -> dict[str, CircuitStatus]:
        """
        Get status of all circuit breakers.

        Returns:
            Dictionary mapping target_id to circuit status
        """
        if limit is not None and limit <= 0:
            raise ValueError("circuit listing limit must be positive")
        circuits: dict[str, CircuitStatus] = {}
        pattern = "circuit:*"

        async for raw_key in self.redis.scan_iter(match=pattern):
            key = _STRING_ADAPTER.validate_python(raw_key)
            target_id = key.removeprefix("circuit:")
            raw = await self.redis.hgetall(key)
            if not raw:
                continue
            status = self._status_from_data(_HASH_ADAPTER.validate_python(raw))
            circuits[target_id] = status
            if limit is not None and len(circuits) >= limit:
                break

        return circuits

    async def add_to_dlq(
        self, target_id: str, message_id: str, payload: JsonObject, reason: str
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
                "payload": json.dumps(payload),
                "reason": reason,
                "timestamp": str(self._clock()),
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

    async def get_dlq_messages(self, count: int = 100) -> list[DLQMessage]:
        """
        Get messages from Dead Letter Queue.

        Args:
            count: Maximum number of messages to retrieve

        Returns:
            List of DLQ messages
        """
        dlq_key = "dlq:messages"
        messages = _STREAM_ADAPTER.validate_python(
            await self.redis.xrange(dlq_key, "-", "+", count=count)
        )

        result: list[DLQMessage] = []
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
