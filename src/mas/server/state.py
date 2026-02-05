"""Redis-backed agent state store."""

from __future__ import annotations

from ..redis_types import AsyncRedisProtocol


class StateStore:
    """Persist and load per-agent state."""

    def __init__(self, redis: AsyncRedisProtocol) -> None:
        """Initialize state store."""
        self._redis = redis

    async def get_state(self, *, agent_id: str) -> dict[str, str]:
        """Return persisted state for an agent."""
        return await self._redis.hgetall(f"agent.state:{agent_id}")

    async def update_state(self, *, agent_id: str, updates: dict[str, str]) -> None:
        """Update persisted agent state with provided fields."""
        if updates:
            await self._redis.hset(f"agent.state:{agent_id}", mapping=updates)

    async def reset_state(self, *, agent_id: str) -> None:
        """Clear persisted agent state."""
        await self._redis.delete(f"agent.state:{agent_id}")
