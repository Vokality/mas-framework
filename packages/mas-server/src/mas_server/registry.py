"""Agent registry and discovery service."""

from __future__ import annotations

import json
import time

from mas_core.sessions import SessionLeaseSettings, SessionLeaseStore
from redis.asyncio import Redis

from .types import AgentDefinition, AgentDiscoveryRecord


class RegistryService:
    """Manage allowlisted agents and discovery records."""

    def __init__(self, *, redis: Redis, agents: dict[str, AgentDefinition]) -> None:
        """Initialize registry service."""
        self._redis = redis
        self._agents = agents
        self._leases = SessionLeaseStore(redis, SessionLeaseSettings())

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
                    "registered_at": now,
                },
            )

        await pipe.execute()

    async def discover(
        self,
        *,
        agent_id: str,
        capabilities: list[str],
    ) -> list[AgentDiscoveryRecord]:
        """List discoverable agents for a sender and capability filter."""
        allowed = await self._redis.smembers(f"agent:{agent_id}:allowed_targets")
        blocked = await self._redis.smembers(f"agent:{agent_id}:blocked_targets")

        if "*" in allowed:
            candidates = list(self._agents.keys())
        else:
            candidates = [target for target in allowed if target]

        candidates = sorted(
            target
            for target in candidates
            if target not in blocked
            and target in self._agents
            and (
                not capabilities
                or any(
                    capability in self._agents[target].capabilities
                    for capability in capabilities
                )
            )
        )

        if not candidates:
            return []
        results: list[AgentDiscoveryRecord] = []
        for target in candidates:
            if not await self._leases.active(target):
                continue

            definition = self._agents[target]

            results.append(
                {
                    "id": definition.agent_id,
                    "capabilities": list(definition.capabilities),
                    "metadata": definition.metadata.copy(),
                    "status": "ACTIVE",
                }
            )

        return results
