"""Redis-based agent registry."""

import json
import secrets
import time
from redis.asyncio import Redis


class AgentRegistry:
    """Manages agent registration in Redis."""

    def __init__(self, redis: Redis):
        """
        Initialize agent registry.

        Args:
            redis: Redis client instance
        """
        self.redis = redis

    async def register(
        self, agent_id: str, capabilities: list[str], metadata: dict | None = None
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

        agent_data = {
            "id": agent_id,
            "capabilities": json.dumps(capabilities),
            "metadata": json.dumps(metadata or {}),
            "status": "ACTIVE",
            "token": token,
            "registered_at": str(time.time()),
        }

        await self.redis.hset(f"agent:{agent_id}", mapping=agent_data)  # type: ignore
        return token

    async def deregister(self, agent_id: str, keep_state: bool = True) -> None:
        """
        Deregister an agent.

        Args:
            agent_id: Agent identifier to deregister
            keep_state: If True, preserves agent state in Redis (default: True)
        """
        await self.redis.delete(f"agent:{agent_id}")
        await self.redis.delete(f"agent:{agent_id}:heartbeat")

        # Only delete state if explicitly requested
        if not keep_state:
            await self.redis.delete(f"agent.state:{agent_id}")

    async def get_agent(self, agent_id: str) -> dict | None:
        """
        Get agent information.

        Args:
            agent_id: Agent identifier

        Returns:
            Agent data dict or None if not found
        """
        data = await self.redis.hgetall(f"agent:{agent_id}")  # type: ignore
        if not data:
            return None

        return {
            "id": data["id"],
            "capabilities": json.loads(data["capabilities"]),
            "metadata": json.loads(data.get("metadata", "{}")),
            "status": data["status"],
            "registered_at": float(data["registered_at"]),
        }

    async def discover(self, capabilities: list[str] | None = None) -> list[dict]:
        """
        Discover agents by capabilities.

        Args:
            capabilities: Optional list of required capabilities.
                         If None, returns all active agents.

        Returns:
            List of agent data dicts
        """
        agents = []
        pattern = "agent:*"

        async for key in self.redis.scan_iter(pattern):
            # Skip non-agent keys (like agent:id:heartbeat)
            if key.count(":") != 1:
                continue

            agent_data = await self.redis.hgetall(key)  # type: ignore
            if not agent_data or agent_data.get("status") != "ACTIVE":
                continue

            agent_caps = json.loads(agent_data.get("capabilities", "[]"))

            # Filter by capabilities if specified
            if capabilities and not any(cap in agent_caps for cap in capabilities):
                continue

            agents.append(
                {
                    "id": agent_data["id"],
                    "capabilities": agent_caps,
                    "metadata": json.loads(agent_data.get("metadata", "{}")),
                }
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
