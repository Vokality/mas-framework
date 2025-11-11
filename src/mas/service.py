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
    - Health monitoring

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
        """Listen for system messages (register, deregister)."""
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
            case _:
                logger.warning("Unknown message type", extra={"type": msg.get("type")})

    async def _monitor_health(self) -> None:
        """Monitor agent health via heartbeats."""
        while self._running:
            try:
                if not self._redis:
                    await asyncio.sleep(30)
                    continue

                # Find stale agents by existing heartbeat keys (expiring soon or invalid TTL)
                async for key in self._redis.scan_iter(match="agent:*:heartbeat"):
                    ttl = await self._redis.ttl(key)
                    if ttl <= 0:  # -2 (missing) or -1 (no expiry) or invalid
                        agent_id = key.split(":")[1]
                        logger.warning(
                            "Agent heartbeat expired", extra={"agent_id": agent_id}
                        )
                        # Mark as inactive if still present
                        agent_key = f"agent:{agent_id}"
                        exists = await self._redis.exists(agent_key)
                        if exists:
                            status = await self._redis.hget(agent_key, "status")
                            if status != "INACTIVE":
                                await self._redis.hset(
                                    agent_key, mapping={"status": "INACTIVE"}
                                )

                # Also detect agents with missing heartbeat keys entirely (with grace period)
                async for agent_key in self._redis.scan_iter(match="agent:*"):
                    # Skip non-agent hashes like heartbeat keys themselves
                    if agent_key.count(":") != 1:
                        continue

                    hb_key = f"{agent_key}:heartbeat"
                    if await self._redis.exists(hb_key):
                        continue

                    reg_at_raw = await self._redis.hget(agent_key, "registered_at")
                    reg_at: Optional[float] = None
                    if isinstance(reg_at_raw, str):
                        try:
                            reg_at = float(reg_at_raw)
                        except ValueError:
                            reg_at = None

                    if reg_at is None:
                        continue

                    if (time.time() - reg_at) <= float(self.heartbeat_timeout):
                        continue

                    status = await self._redis.hget(agent_key, "status")
                    if status != "INACTIVE":
                        await self._redis.hset(
                            agent_key,
                            mapping={"status": "INACTIVE"},
                        )

                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error("Health monitoring error", exc_info=e)
                await asyncio.sleep(30)
