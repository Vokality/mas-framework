"""Agent registry and discovery service."""

from __future__ import annotations

import json
import time
from typing import Any

from ..redis_types import AsyncRedisProtocol
from .types import AgentDefinition


class RegistryService:
    """Manage allowlisted agents and discovery records."""

    def __init__(
        self, *, redis: AsyncRedisProtocol, agents: dict[str, AgentDefinition]
    ) -> None:
        """Initialize registry service."""
        self._redis = redis
        self._agents = agents

    async def bootstrap_registry(self) -> None:
        """Populate Redis agent records from allowlist."""
        now = str(time.time())
        pipe = self._redis.pipeline()

        for agent_id, definition in self._agents.items():
            pipe.hset(
                f"agent:{agent_id}",
                mapping={
                    "id": agent_id,
                    "capabilities": json.dumps(definition.capabilities),
                    "metadata": json.dumps(definition.metadata),
                    "status": "INACTIVE",
                    "registered_at": now,
                },
            )

        await pipe.execute()

    async def set_agent_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status field in Redis."""
        await self._redis.hset(
            f"agent:{agent_id}",
            mapping={"status": status, "registered_at": str(time.time())},
        )

    async def discover(
        self,
        *,
        agent_id: str,
        capabilities: list[str],
    ) -> list[dict[str, Any]]:
        """List discoverable agents for a sender and capability filter."""
        allowed = await self._redis.smembers(f"agent:{agent_id}:allowed_targets")
        blocked = await self._redis.smembers(f"agent:{agent_id}:blocked_targets")

        if "*" in allowed:
            candidates = list(self._agents.keys())
        else:
            candidates = [target for target in allowed if target]

        candidates = [target for target in candidates if target not in blocked]

        results: list[dict[str, Any]] = []
        for target in candidates:
            if target not in self._agents:
                continue

            status = await self._redis.hget(f"agent:{target}", "status")
            if status != "ACTIVE":
                continue

            definition = self._agents[target]
            if capabilities and not any(
                capability in definition.capabilities for capability in capabilities
            ):
                continue

            results.append(
                {
                    "id": definition.agent_id,
                    "capabilities": list(definition.capabilities),
                    "metadata": dict(definition.metadata),
                    "status": status,
                }
            )

        return results
