"""MAS Service - Lightweight registry and discovery service."""

import asyncio
import json
import logging
from typing import Optional, Any
from redis.asyncio import Redis

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
        self._redis: Optional[Redis] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the MAS service."""
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)
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

        pubsub = self._redis.pubsub()
        await pubsub.subscribe("mas.system")

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break

                if message["type"] != "message":
                    continue

                try:
                    msg = json.loads(message["data"])
                    await self._handle_message(msg)
                except Exception as e:
                    logger.error("Failed to handle system message", exc_info=e)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def _handle_message(self, msg: dict) -> None:
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
                async for key in self._redis.scan_iter("agent:*:heartbeat"):
                    _ttl_obj = self._redis.ttl(key)
                    ttl_any: Any = (
                        await _ttl_obj if asyncio.iscoroutine(_ttl_obj) else _ttl_obj
                    )
                    ttl_int: int = int(ttl_any)
                    if ttl_int <= 0:  # -2 (missing) or -1 (no expiry) or invalid
                        agent_id = key.split(":")[1]
                        logger.warning(
                            "Agent heartbeat expired", extra={"agent_id": agent_id}
                        )
                        # Mark as inactive if still present
                        agent_key = f"agent:{agent_id}"
                        _exists = self._redis.exists(agent_key)
                        exists = (
                            await _exists if asyncio.iscoroutine(_exists) else _exists
                        )
                        if exists:
                            _status = self._redis.hget(agent_key, "status")
                            status = (
                                await _status
                                if asyncio.iscoroutine(_status)
                                else _status
                            )
                            if status != "INACTIVE":
                                _result = self._redis.hset(
                                    agent_key, "status", "INACTIVE"
                                )
                                if asyncio.iscoroutine(_result):
                                    await _result

                # Also detect agents with missing heartbeat keys entirely (with grace period)
                async for agent_key in self._redis.scan_iter("agent:*"):
                    # Skip non-agent hashes like heartbeat keys themselves
                    if agent_key.count(":") != 1:
                        continue
                    hb_key = f"{agent_key}:heartbeat"
                    _hb_exists = self._redis.exists(hb_key)
                    hb_exists = (
                        await _hb_exists
                        if asyncio.iscoroutine(_hb_exists)
                        else _hb_exists
                    )
                    if not hb_exists:
                        # If the agent exists but no heartbeat yet, only mark INACTIVE
                        # if it has exceeded the heartbeat timeout since registration.
                        _reg_at = self._redis.hget(agent_key, "registered_at")
                        _reg_at_val: Any = (
                            await _reg_at if asyncio.iscoroutine(_reg_at) else _reg_at
                        )
                        reg_at_str: Optional[str]
                        if isinstance(_reg_at_val, bytes):
                            try:
                                reg_at_str = _reg_at_val.decode()
                            except Exception:
                                reg_at_str = None
                        elif isinstance(_reg_at_val, str):
                            reg_at_str = _reg_at_val
                        else:
                            reg_at_str = None

                        try:
                            reg_at = (
                                float(reg_at_str) if reg_at_str is not None else None
                            )
                        except (TypeError, ValueError):
                            reg_at = None

                        # Determine if grace period has elapsed
                        grace_elapsed = False
                        if reg_at is not None:
                            try:
                                import time as _time

                                grace_elapsed = (_time.time() - reg_at) > float(
                                    self.heartbeat_timeout
                                )
                            except Exception:
                                grace_elapsed = False

                        if grace_elapsed:
                            _status2 = self._redis.hget(agent_key, "status")
                            status = (
                                await _status2
                                if asyncio.iscoroutine(_status2)
                                else _status2
                            )
                            if status != "INACTIVE":
                                _result2 = self._redis.hset(
                                    agent_key, "status", "INACTIVE"
                                )
                                if asyncio.iscoroutine(_result2):
                                    await _result2

                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error("Health monitoring error", exc_info=e)
                await asyncio.sleep(30)
