"""MAS Service - Lightweight registry and discovery service."""

import asyncio
import json
import logging
import time
from typing import Any, Optional, cast

from .redis_client import create_redis_client
from .redis_types import AsyncRedisProtocol, PubSubProtocol

logger = logging.getLogger(__name__)


class MASService:
    """
    Lightweight MAS service that manages agent registry and discovery.

    Agents communicate peer-to-peer. This service only handles:
    - Agent registration
    - Agent discovery
    - Health monitoring (with multi-instance support)

    Multi-instance health monitoring:
    - Each agent instance maintains its own heartbeat
    - An agent is considered healthy if at least one instance has a valid heartbeat
    - An agent is marked INACTIVE only when ALL instances are unhealthy

    Usage:
        service = MASService(redis_url="redis://localhost:6379")
        await service.start()
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        heartbeat_timeout: int = 60,
    ):
        """
        Initialize MAS service.

        Args:
            redis_url: Redis connection URL
            heartbeat_timeout: Agent heartbeat timeout in seconds
        """
        self.redis_url = redis_url
        self.heartbeat_timeout = heartbeat_timeout
        self._redis: Optional[AsyncRedisProtocol] = None
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start the MAS service."""
        self._redis = create_redis_client(url=self.redis_url, decode_responses=True)
        self._running = True

        logger.info("MAS Service starting", extra={"redis_url": self.redis_url})

        # Start background tasks
        self._tasks.append(asyncio.create_task(self._monitor_health()))
        self._tasks.append(asyncio.create_task(self._handle_system_messages()))

        logger.info("MAS Service started")

    async def stop(self) -> None:
        """Stop the MAS service."""
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._redis:
            await self._redis.aclose()

        logger.info("MAS Service stopped")

    async def _handle_system_messages(self) -> None:
        """Listen for system messages (register, deregister, instance events)."""
        if not self._redis:
            return

        pubsub: PubSubProtocol = self._redis.pubsub()
        await pubsub.subscribe("mas.system")

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break

                message_dict = dict(message)
                if message_dict.get("type") != "message":
                    continue

                data_raw = message_dict.get("data")
                if isinstance(data_raw, bytes):
                    try:
                        data_text = data_raw.decode()
                    except UnicodeDecodeError:
                        continue
                elif isinstance(data_raw, str):
                    data_text = data_raw
                else:
                    continue

                try:
                    parsed = json.loads(data_text)
                except json.JSONDecodeError:
                    logger.warning("Invalid system message payload")
                    continue

                if not isinstance(parsed, dict):
                    logger.warning("Unexpected system message format")
                    continue

                msg = cast(dict[str, Any], parsed)
                try:
                    await self._handle_message(msg)
                except Exception as exc:
                    logger.error("Failed to handle system message", exc_info=exc)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle system messages."""
        match msg.get("type"):
            case "REGISTER":
                logger.info(
                    "Agent registered",
                    extra={
                        "agent_id": msg["agent_id"],
                        "capabilities": msg.get("capabilities", []),
                    },
                )
            case "DEREGISTER":
                logger.info("Agent deregistered", extra={"agent_id": msg["agent_id"]})
            case "INSTANCE_JOIN":
                logger.info(
                    "Agent instance joined",
                    extra={
                        "agent_id": msg["agent_id"],
                        "instance_id": msg.get("instance_id"),
                        "instance_count": msg.get("instance_count"),
                    },
                )
            case "INSTANCE_LEAVE":
                logger.info(
                    "Agent instance left",
                    extra={
                        "agent_id": msg["agent_id"],
                        "instance_id": msg.get("instance_id"),
                    },
                )
            case _:
                logger.warning("Unknown message type", extra={"type": msg.get("type")})

    async def _monitor_health(self) -> None:
        """Monitor agent health via per-instance heartbeats.

        Multi-instance health monitoring:
        1. Scan for all agent keys
        2. For each agent, check all instance heartbeats
        3. Agent is healthy if ANY instance has a valid heartbeat
        4. Agent is marked INACTIVE only when ALL instances are unhealthy

        Uses pipeline batching to reduce Redis round-trips.
        """
        while self._running:
            try:
                if not self._redis:
                    await asyncio.sleep(30)
                    continue

                # Phase 1: Collect all agent keys (single scan)
                agent_keys: list[str] = []
                async for key in self._redis.scan_iter(match="agent:*"):
                    # Only include agent hashes, not heartbeat or instance_count keys
                    if key.count(":") == 1:
                        agent_keys.append(key)

                if not agent_keys:
                    await asyncio.sleep(30)
                    continue

                # Phase 2: Get agent statuses and registration times
                pipe = self._redis.pipeline()
                for agent_key in agent_keys:
                    pipe.hget(agent_key, "status")
                    pipe.hget(agent_key, "registered_at")

                agent_info_results = await pipe.execute()

                # Phase 3: For each agent, collect all instance heartbeat keys
                current_time = time.time()
                agents_to_check: list[tuple[str, str, float | None]] = []

                for i, agent_key in enumerate(agent_keys):
                    status = agent_info_results[i * 2]
                    reg_at_raw = agent_info_results[i * 2 + 1]

                    # Skip if already inactive
                    if status == "INACTIVE":
                        continue

                    reg_at: float | None = None
                    if isinstance(reg_at_raw, str):
                        try:
                            reg_at = float(reg_at_raw)
                        except ValueError:
                            pass

                    agent_id = agent_key.split(":")[1]
                    agents_to_check.append((agent_key, agent_id, reg_at))

                if not agents_to_check:
                    await asyncio.sleep(30)
                    continue

                # Phase 4: Collect all heartbeat keys for agents to check
                agent_heartbeat_keys: dict[str, list[str]] = {}
                for agent_key, agent_id, _ in agents_to_check:
                    heartbeat_keys: list[str] = []
                    async for hb_key in self._redis.scan_iter(
                        match=f"agent:{agent_id}:heartbeat:*"
                    ):
                        heartbeat_keys.append(hb_key)
                    agent_heartbeat_keys[agent_id] = heartbeat_keys

                # Phase 5: Batch fetch all heartbeat TTLs
                all_hb_keys: list[str] = []
                key_to_agent: dict[str, str] = {}
                for agent_id, hb_keys in agent_heartbeat_keys.items():
                    for hb_key in hb_keys:
                        all_hb_keys.append(hb_key)
                        key_to_agent[hb_key] = agent_id

                agent_has_healthy_instance: dict[str, bool] = {
                    agent_id: False for _, agent_id, _ in agents_to_check
                }

                if all_hb_keys:
                    ttl_pipe = self._redis.pipeline()
                    for hb_key in all_hb_keys:
                        ttl_pipe.ttl(hb_key)

                    ttls = await ttl_pipe.execute()

                    # Determine which agents have at least one healthy instance
                    for hb_key, ttl in zip(all_hb_keys, ttls, strict=True):
                        agent_id = key_to_agent[hb_key]
                        # TTL > 0 means the heartbeat is valid
                        if ttl is not None and ttl > 0:
                            agent_has_healthy_instance[agent_id] = True

                # Phase 6: Determine which agents should be deactivated
                agents_to_deactivate: list[str] = []

                for agent_key, agent_id, reg_at in agents_to_check:
                    has_heartbeat_keys = len(agent_heartbeat_keys.get(agent_id, [])) > 0
                    is_healthy = agent_has_healthy_instance.get(agent_id, False)

                    if is_healthy:
                        # At least one instance is healthy
                        continue

                    if not has_heartbeat_keys:
                        # No heartbeat keys exist - check grace period for new agents
                        if reg_at is not None:
                            if (current_time - reg_at) > float(self.heartbeat_timeout):
                                logger.warning(
                                    "Agent has no healthy instances (grace period expired)",
                                    extra={"agent_id": agent_id},
                                )
                                agents_to_deactivate.append(agent_key)
                        # If no reg_at, skip (shouldn't happen but be safe)
                    else:
                        # Has heartbeat keys but none are healthy (all expired)
                        logger.warning(
                            "All agent instances unhealthy",
                            extra={"agent_id": agent_id},
                        )
                        agents_to_deactivate.append(agent_key)

                # Phase 7: Batch update stale agents using pipeline
                if agents_to_deactivate:
                    update_pipe = self._redis.pipeline()
                    for agent_key in agents_to_deactivate:
                        update_pipe.hset(agent_key, mapping={"status": "INACTIVE"})
                    await update_pipe.execute()

                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error("Health monitoring error", exc_info=e)
                await asyncio.sleep(30)
