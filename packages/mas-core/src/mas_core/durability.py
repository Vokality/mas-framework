"""Explicit confirmation of Redis writes on their owning connection."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError

_COUNT = TypeAdapter(int)
_COUNTS = TypeAdapter(list[int])
_RESPONSES = TypeAdapter(list[object])


@dataclass(frozen=True, slots=True)
class RedisDurabilitySettings:
    """Required replica acknowledgements and optional AOF persistence."""

    replica_count: int = 0
    timeout_ms: int = 1000
    wait_for_aof: bool = False

    def __post_init__(self) -> None:
        """Reject unbounded waits and invalid acknowledgement requirements."""
        if self.replica_count < 0:
            raise ValueError("replica_count must be non-negative")
        if self.timeout_ms <= 0:
            raise ValueError("durability timeout_ms must be positive")


class RedisDurabilityError(RedisError):
    """A write may have committed without satisfying configured durability."""


class RedisDurability:
    """Confirm writes without silently accepting a weaker storage guarantee."""

    def __init__(
        self, settings: RedisDurabilitySettings = RedisDurabilitySettings()
    ) -> None:
        """Bind confirmation to an explicit, immutable storage policy."""
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """Whether a write needs replication or persistence confirmation."""
        return self._settings.wait_for_aof or self._settings.replica_count > 0

    async def confirm(self, connection: Redis) -> None:
        """Confirm preceding writes on this same exclusive Redis connection.

        Callers must retain the connection from the write through this call.
        Failure represents an uncertain commit, not a rolled-back operation.
        """
        if not self.enabled:
            return
        try:
            if self._settings.wait_for_aof:
                response = await connection.waitaof(
                    1, self._settings.replica_count, self._settings.timeout_ms
                )
            else:
                response = await connection.wait(
                    self._settings.replica_count, self._settings.timeout_ms
                )
            self._validate_confirmation(response)
        except (RedisError, ValidationError) as error:
            raise RedisDurabilityError("configured_durability_unconfirmed") from error

    async def execute(self, pipeline: Pipeline) -> list[object]:
        """Execute queued writes and their barrier on one pipeline connection.

        The pipeline must be nontransactional so WAIT/WAITAOF can block. Redis
        processes its writes before the final confirmation on the same socket.
        A failed command or barrier may still leave committed writes.
        """
        if (
            pipeline.is_transaction
            or pipeline.explicit_transaction
            or pipeline.watching
        ):
            raise ValueError("durability requires a nontransactional pipeline")
        if not pipeline.command_stack:
            raise ValueError("durability requires queued writes")
        if self._settings.wait_for_aof:
            pipeline.waitaof(1, self._settings.replica_count, self._settings.timeout_ms)
        elif self.enabled:
            pipeline.wait(self._settings.replica_count, self._settings.timeout_ms)
        try:
            responses = _RESPONSES.validate_python(
                await pipeline.execute(), strict=True
            )
            if self.enabled:
                if not responses:
                    raise RedisDurabilityError("configured_durability_unconfirmed")
                self._validate_confirmation(responses.pop())
            return responses
        except (RedisError, ValidationError) as error:
            if not self.enabled and isinstance(error, RedisError):
                raise
            raise RedisDurabilityError("configured_durability_unconfirmed") from error

    def _validate_confirmation(self, response: object) -> None:
        """Validate the exact acknowledgement shape and configured thresholds."""
        if self._settings.wait_for_aof:
            counts = _COUNTS.validate_python(response, strict=True)
            confirmed = (
                len(counts) == 2
                and counts[0] >= 1
                and counts[1] >= self._settings.replica_count
            )
        else:
            replicas = _COUNT.validate_python(response, strict=True)
            confirmed = replicas >= self._settings.replica_count
        if not confirmed:
            raise RedisDurabilityError("configured_durability_unconfirmed")
