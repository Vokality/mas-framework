"""Redis-based agent registry."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Optional, TypedDict

from .redis_types import AsyncRedisProtocol

__all__ = ["AgentRegistry", "AgentRecord"]


class _AgentRecordRequired(TypedDict):
    id: str
    capabilities: list[str]
    metadata: dict[str, Any]


class AgentRecord(_AgentRecordRequired, total=False):
    """Typed representation of an agent registry record."""

    status: str
    registered_at: float


class AgentRegistry:
    """Manages agent registration in Redis."""

    def __init__(self, redis: AsyncRedisProtocol):
        """
        Initialize agent registry.

        Args:
            redis: Redis client instance
        """
        self.redis: AsyncRedisProtocol = redis

    async def register(
        self,
        agent_id: str,
        capabilities: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Register an agent.

        Args:
            agent_id: Unique agent identifier
            capabilities: List of agent capabilities
            metadata: Optional agent metadata

        Returns:
            Authentication token for the agent
        """
        token = self._generate_token()

        agent_data: dict[str, str] = {
            "id": agent_id,
            "capabilities": json.dumps(capabilities),
            "metadata": json.dumps(metadata or {}),
            "status": "ACTIVE",
            "token": token,
            "registered_at": str(time.time()),
        }

        await self.redis.hset(f"agent:{agent_id}", mapping=agent_data)
        return token

    async def deregister(self, agent_id: str, keep_state: bool = True) -> None:
        """
        Deregister an agent.

        Uses pipeline to batch delete operations into a single round-trip.

        Args:
            agent_id: Agent identifier to deregister
            keep_state: If True, preserves agent state in Redis (default: True)
        """
        # Batch all deletes into a single pipeline
        pipe = self.redis.pipeline()
        pipe.delete(f"agent:{agent_id}")
        pipe.delete(f"agent:{agent_id}:heartbeat")

        # Only delete state if explicitly requested
        if not keep_state:
            pipe.delete(f"agent.state:{agent_id}")

        await pipe.execute()

    async def get_agent(self, agent_id: str) -> AgentRecord | None:
        """
        Get agent information.

        Args:
            agent_id: Agent identifier

        Returns:
            Agent data dict or None if not found
        """
        data = await self.redis.hgetall(f"agent:{agent_id}")
        if not data:
            return None

        return AgentRecord(
            id=data["id"],
            capabilities=json.loads(data["capabilities"]),
            metadata=json.loads(data.get("metadata", "{}")),
            status=data["status"],
            registered_at=float(data["registered_at"]),
        )

    async def discover(
        self, capabilities: list[str] | None = None
    ) -> list[AgentRecord]:
        """
        Discover agents by capabilities.

        Uses pipeline batching to fetch all agent data in a single round-trip,
        eliminating the N+1 query pattern.

        Args:
            capabilities: Optional list of required capabilities.
                         If None, returns all active agents.

        Returns:
            List of agent data dicts
        """
        # Phase 1: Collect all matching keys
        keys: list[str] = []
        pattern = "agent:*"

        async for key in self.redis.scan_iter(match=pattern):
            # Skip non-agent keys (like agent:id:heartbeat)
            if not key.startswith("agent:") or key.count(":") != 1:
                continue
            keys.append(key)

        if not keys:
            return []

        # Phase 2: Batch fetch all agent data using pipeline
        pipe = self.redis.pipeline()
        for key in keys:
            pipe.hgetall(key)

        results = await pipe.execute()

        # Phase 3: Process results and filter
        agents: list[AgentRecord] = []
        for agent_data in results:
            if not agent_data or agent_data.get("status") != "ACTIVE":
                continue

            agent_caps = json.loads(agent_data.get("capabilities", "[]"))

            # Filter by capabilities if specified
            if capabilities and not any(cap in agent_caps for cap in capabilities):
                continue

            agents.append(
                AgentRecord(
                    id=agent_data["id"],
                    capabilities=agent_caps,
                    metadata=json.loads(agent_data.get("metadata", "{}")),
                )
            )

        return agents

    async def update_heartbeat(self, agent_id: str, ttl: int = 60) -> None:
        """
        Update agent heartbeat.

        Args:
            agent_id: Agent identifier
            ttl: Time-to-live in seconds (default: 60)
        """
        await self.redis.setex(f"agent:{agent_id}:heartbeat", ttl, str(time.time()))

    def _generate_token(self) -> str:
        """Generate authentication token."""
        return secrets.token_urlsafe(32)
