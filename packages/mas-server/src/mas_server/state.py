"""Redis-backed agent state store."""

from __future__ import annotations

from dataclasses import dataclass

import grpc
from mas_core.durability import RedisDurability, RedisDurabilitySettings
from pydantic import TypeAdapter
from redis.asyncio import Redis

from .errors import RpcError

_STATE_ADAPTER = TypeAdapter(dict[str, str])
_SNAPSHOT_ADAPTER = TypeAdapter(tuple[int, list[str]])
_REVISION_ADAPTER = TypeAdapter(int)
_VALIDATE_STATE = """
local revision_text = redis.call('GET', KEYS[2]) or '0'
local revision = tonumber(revision_text)
if not string.match(revision_text, '^%d+$') or not revision
    or revision > 9007199254740990 then
    return redis.error_reply('invalid_state_revision')
end
local state_type = redis.call('TYPE', KEYS[1]).ok
if state_type ~= 'none' and state_type ~= 'hash' then
    return redis.error_reply('invalid_state_storage_type')
end
"""
_READ = (
    _VALIDATE_STATE
    + """
return {revision, redis.call('HGETALL', KEYS[1])}
"""
)
_UPDATE = (
    _VALIDATE_STATE
    + """
if revision ~= tonumber(ARGV[1]) then return -1 end
if #ARGV > 1 then
    redis.call('HSET', KEYS[1], unpack(ARGV, 2))
    revision = redis.call('INCR', KEYS[2])
end
return revision
"""
)
_RESET = (
    _VALIDATE_STATE
    + """
if revision ~= tonumber(ARGV[1]) then return -1 end
redis.call('DEL', KEYS[1])
return redis.call('INCR', KEYS[2])
"""
)


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Persisted fields paired with the revision used for optimistic writes."""

    fields: dict[str, str]
    revision: int


class StateStore:
    """Persist and load per-agent state."""

    def __init__(
        self,
        redis: Redis,
        *,
        durability: RedisDurabilitySettings = RedisDurabilitySettings(),
    ) -> None:
        """Initialize state store."""
        self._redis = redis
        self._durability = RedisDurability(settings=durability)

    async def get_state(self, *, agent_id: str) -> dict[str, str]:
        """Return persisted state for an agent."""
        return (await self.snapshot(agent_id=agent_id)).fields

    async def snapshot(self, *, agent_id: str) -> StateSnapshot:
        """Read fields and revision from the same atomic Redis snapshot."""
        revision, pairs = _SNAPSHOT_ADAPTER.validate_python(
            await self._redis.eval(
                _READ,
                2,
                f"agent.state:{agent_id}",
                f"agent.state.revision:{agent_id}",
            )
        )
        return StateSnapshot(
            _STATE_ADAPTER.validate_python(
                dict(zip(pairs[::2], pairs[1::2], strict=True))
            ),
            revision,
        )

    async def update_state(
        self, *, agent_id: str, updates: dict[str, str], expected_revision: int
    ) -> int:
        """Update persisted agent state with provided fields."""
        arguments = [item for pair in updates.items() for item in pair]
        async with self._redis.client() as connection:
            revision = _REVISION_ADAPTER.validate_python(
                await connection.eval(
                    _UPDATE,
                    2,
                    f"agent.state:{agent_id}",
                    f"agent.state.revision:{agent_id}",
                    expected_revision,
                    *arguments,
                )
            )
            if revision >= 0:
                await self._durability.confirm(connection)
        if revision < 0:
            raise RpcError(grpc.StatusCode.ABORTED, "state_revision_conflict")
        return revision

    async def reset_state(self, *, agent_id: str, expected_revision: int) -> int:
        """Clear persisted agent state."""
        async with self._redis.client() as connection:
            revision = _REVISION_ADAPTER.validate_python(
                await connection.eval(
                    _RESET,
                    2,
                    f"agent.state:{agent_id}",
                    f"agent.state.revision:{agent_id}",
                    expected_revision,
                )
            )
            if revision >= 0:
                await self._durability.confirm(connection)
        if revision < 0:
            raise RpcError(grpc.StatusCode.ABORTED, "state_revision_conflict")
        return revision
