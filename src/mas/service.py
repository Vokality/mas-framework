"""MAS Service - Lightweight registry and discovery service."""
import asyncio
import json
import logging
from typing import Optional
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
                        "capabilities": msg.get("capabilities", [])
                    }
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
                
                # Find stale agents
                pattern = "agent:*:heartbeat"
                async for key in self._redis.scan_iter(pattern):
                    ttl = await self._redis.ttl(key)
                    if ttl < 0:  # Expired
                        agent_id = key.split(":")[1]
                        logger.warning("Agent heartbeat expired", extra={"agent_id": agent_id})
                        # Mark as inactive
                        agent_key = f"agent:{agent_id}"
                        exists = await self._redis.exists(agent_key)
                        if exists:
                            result = self._redis.hset(agent_key, "status", "INACTIVE")
                            if asyncio.iscoroutine(result):
                                await result
                
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error("Health monitoring error", exc_info=e)
                await asyncio.sleep(30)
