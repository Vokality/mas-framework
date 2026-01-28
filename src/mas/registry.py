"""Redis-based agent registry with multi-instance support."""

from __future__ import annotations

import json
import hashlib
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
    """
    Manages agent registration in Redis with multi-instance support.

    Multi-instance features:
    - Idempotent registration: first instance registers, subsequent instances join
    - Instance counting: tracks active instance count per agent
    - Per-instance heartbeats: each instance maintains its own heartbeat
    - Graceful deregistration: only removes agent when last instance leaves
    """

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
        instance_id: str,
        capabilities: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Register an agent instance.

        Security model:
        - Each instance receives its own bearer token.
        - Redis stores only a SHA-256 hash of the token.
        - The plaintext token is returned once to the caller.

        Args:
            agent_id: Logical agent identifier (shared across instances)
            instance_id: Unique instance identifier
            capabilities: List of agent capabilities
            metadata: Optional agent metadata

        Returns:
            Plaintext bearer token for this specific instance.
        """
        agent_key = f"agent:{agent_id}"
        instance_count_key = f"agent:{agent_id}:instance_count"

        token = self._generate_token()
        token_hash = self._hash_token(token)
        token_hash_key = f"agent:{agent_id}:token_hash:{instance_id}"

        # Check if agent already exists
        existing_data = await self.redis.hgetall(agent_key)

        if existing_data:
            # Agent exists - join as a new instance
            pipe = self.redis.pipeline()
            pipe.incr(instance_count_key)
            pipe.set(token_hash_key, token_hash)
            # Reactivate if previously inactive
            if existing_data.get("status") == "INACTIVE":
                pipe.hset(
                    agent_key,
                    mapping={
                        "status": "ACTIVE",
                        "registered_at": str(time.time()),
                    },
                )
            await pipe.execute()
            await self._ensure_signing_key(agent_id)
            return token

        # First instance - create new registration
        agent_data: dict[str, str] = {
            "id": agent_id,
            "capabilities": json.dumps(capabilities),
            "metadata": json.dumps(metadata or {}),
            "status": "ACTIVE",
            "registered_at": str(time.time()),
        }

        signing_key = self._generate_signing_key()
        signing_key_field = f"agent:{agent_id}:signing_key"

        # Use pipeline for atomic registration
        pipe = self.redis.pipeline()
        pipe.hset(agent_key, mapping=agent_data)
        pipe.incr(instance_count_key)  # Set to 1
        pipe.set(signing_key_field, signing_key)
        pipe.set(token_hash_key, token_hash)
        await pipe.execute()

        return token

    async def deregister(
        self,
        agent_id: str,
        instance_id: str,
        keep_state: bool = True,
    ) -> None:
        """
        Deregister an agent instance.

        Decrements the instance count. Only removes the agent entry when
        the last instance deregisters.

        Args:
            agent_id: Logical agent identifier
            instance_id: Instance identifier being deregistered
            keep_state: If True, preserves agent state in Redis (default: True)
        """
        instance_count_key = f"agent:{agent_id}:instance_count"
        heartbeat_key = f"agent:{agent_id}:heartbeat:{instance_id}"
        token_hash_key = f"agent:{agent_id}:token_hash:{instance_id}"

        # Decrement instance count
        new_count = await self.redis.decr(instance_count_key)

        # Always delete this instance's heartbeat
        await self.redis.delete(heartbeat_key)
        # Always delete this instance's token
        await self.redis.delete(token_hash_key)

        if new_count <= 0:
            # Last instance - clean up agent registration
            pipe = self.redis.pipeline()
            pipe.delete(f"agent:{agent_id}")
            pipe.delete(instance_count_key)
            pipe.delete(f"agent:{agent_id}:signing_key")

            # Clean up any remaining heartbeat keys for this agent
            # (in case of unclean shutdowns)
            async for key in self.redis.scan_iter(
                match=f"agent:{agent_id}:heartbeat:*"
            ):
                pipe.delete(key)

            # Clean up any remaining token hashes (in case of unclean shutdowns)
            async for key in self.redis.scan_iter(
                match=f"agent:{agent_id}:token_hash:*"
            ):
                pipe.delete(key)

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

    async def get_instance_count(self, agent_id: str) -> int:
        """
        Get the number of active instances for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Number of active instances (0 if agent not registered)
        """
        count = await self.redis.get(f"agent:{agent_id}:instance_count")
        if count is None:
            return 0
        return int(count)

    async def discover(
        self, capabilities: list[str] | None = None
    ) -> list[AgentRecord]:
        """
        Discover agents by capabilities.

        Uses pipeline batching to fetch all agent data in a single round-trip,
        eliminating the N+1 query pattern.

        Note: Returns logical agents, not instances. Callers don't need to know
        about individual instances.

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
            # Only include agent hashes, not heartbeat or instance_count keys
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

    async def update_heartbeat(
        self,
        agent_id: str,
        instance_id: str,
        ttl: int = 60,
    ) -> None:
        """
        Update heartbeat for a specific agent instance.

        Each instance maintains its own heartbeat key. The agent is considered
        healthy if at least one instance has a valid heartbeat.

        Args:
            agent_id: Logical agent identifier
            instance_id: Instance identifier
            ttl: Time-to-live in seconds (default: 60)
        """
        heartbeat_key = f"agent:{agent_id}:heartbeat:{instance_id}"
        await self.redis.setex(heartbeat_key, ttl, str(time.time()))

    async def get_instance_heartbeats(self, agent_id: str) -> dict[str, float | None]:
        """
        Get heartbeat TTLs for all instances of an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Dict mapping instance_id to TTL (None if expired/missing)
        """
        heartbeats: dict[str, float | None] = {}
        pattern = f"agent:{agent_id}:heartbeat:*"

        # Collect all heartbeat keys
        keys: list[str] = []
        async for key in self.redis.scan_iter(match=pattern):
            keys.append(key)

        if not keys:
            return heartbeats

        # Batch fetch TTLs
        pipe = self.redis.pipeline()
        for key in keys:
            pipe.ttl(key)

        ttls = await pipe.execute()

        # Extract instance IDs and map to TTLs
        prefix_len = len(f"agent:{agent_id}:heartbeat:")
        for key, ttl in zip(keys, ttls, strict=True):
            instance_id = key[prefix_len:]
            # TTL of -2 means key doesn't exist, -1 means no expiry
            heartbeats[instance_id] = ttl if ttl > 0 else None

        return heartbeats

    async def has_healthy_instance(self, agent_id: str) -> bool:
        """
        Check if an agent has at least one healthy instance.

        An instance is healthy if its heartbeat key exists and has TTL > 0.

        Args:
            agent_id: Agent identifier

        Returns:
            True if at least one instance has a valid heartbeat
        """
        heartbeats = await self.get_instance_heartbeats(agent_id)
        return any(ttl is not None and ttl > 0 for ttl in heartbeats.values())

    async def _ensure_signing_key(self, agent_id: str) -> None:
        key_field = f"agent:{agent_id}:signing_key"
        existing = await self.redis.get(key_field)
        if existing:
            return
        await self.redis.set(key_field, self._generate_signing_key())

    def _generate_token(self) -> str:
        """Generate authentication token."""
        return secrets.token_urlsafe(32)

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _generate_signing_key(self) -> str:
        """Generate per-agent signing key (hex encoded)."""
        return secrets.token_bytes(32).hex()
