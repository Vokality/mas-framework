import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import AsyncGenerator, Dict, Optional, Set

from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from mas.logger import get_logger
from mas.transport.task import TaskManager

logger = get_logger()


@dataclass
class ConnectionState:
    """Tracks the state and health of Redis connections."""

    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0
    is_healthy: bool = False
    last_error: Optional[str] = None
    active_channels: Set[str] = field(default_factory=set)

    def __post_init__(self):
        self.active_channels = set()


class RedisConnectionManager:
    """Manages Redis connections and their lifecycle."""

    def __init__(
        self,
        url: str = "redis://localhost",
        pool_size: int = 10,
        health_check_interval: int = 30,
        max_reconnect_attempts: int = 3,
        pool: Optional[ConnectionPool] = None,
    ) -> None:
        self.url = url
        self.pool_size = pool_size
        self.health_check_interval = health_check_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self._pool = pool
        self._pubsubs: Dict[str, PubSub] = {}
        self._state = ConnectionState()
        self._running = False
        self._task_manager = TaskManager()
        self._cleanup_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    async def _create_pool(self) -> None:
        """Create and validate connection pool."""
        if self._pool:
            await self._pool.disconnect()

        self._pool = ConnectionPool.from_url(
            self.url,
            max_connections=self.pool_size,
            decode_responses=True,
        )

        # Validate pool with test connection
        redis = Redis(connection_pool=self._pool)
        try:
            await redis.ping()
        finally:
            await redis.aclose()

    async def initialize(self) -> None:
        """Initialize the connection manager."""
        if self._running:
            logger.debug("Connection manager already initialized")
            return

        try:
            logger.info("Creating Redis connection pool")
            await self._create_pool()
            self._state.is_healthy = True
            self._running = True

            # Start health check task with proper tracking
            await self._task_manager.create_task(
                "health_check",
                self._health_check_loop(),
                timeout=self.health_check_interval * 2,  # Double interval as timeout
            )
            logger.info("Connection manager initialization completed")

        except RedisError as e:
            logger.error(f"Connection manager initialization failed: {e}")
            if self._pool:
                await self._pool.disconnect()
                self._pool = None
            raise

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[Redis, None]:
        """Get a Redis connection from the pool."""
        if not self._running:
            raise RuntimeError("Connection manager not initialized or shutting down")

        if not self._pool:
            raise RuntimeError("Connection pool not initialized")

        redis = Redis(connection_pool=self._pool)
        try:
            yield redis
        finally:
            await redis.aclose()

    async def get_pubsub(self, channel: str) -> PubSub:
        """Get or create a PubSub instance for a channel."""
        if not self._running:
            raise RuntimeError("Connection manager not initialized or shutting down")

        if channel in self._pubsubs:
            return self._pubsubs[channel]

        async with self._cleanup_lock:
            # Double check after acquiring lock
            if channel in self._pubsubs:
                return self._pubsubs[channel]

            try:
                async with self.get_connection() as redis:
                    pubsub = redis.pubsub()
                    await pubsub.subscribe(channel)
                    self._pubsubs[channel] = pubsub
                    self._state.active_channels.add(channel)
                    logger.info(f"Created new PubSub for channel: {channel}")
                    return pubsub
            except Exception as e:
                logger.error(f"Failed to create PubSub for channel {channel}: {e}")
                raise

    async def _health_check_loop(self) -> None:
        """Periodic health check with reconnection attempts."""
        while self._running:
            try:
                async with self.get_connection() as redis:
                    await redis.ping()

                self._state.last_health_check = datetime.now(UTC)
                self._state.is_healthy = True
                self._state.consecutive_failures = 0

            except RedisError as e:
                self._state.consecutive_failures += 1
                self._state.is_healthy = False
                self._state.last_error = str(e)
                logger.error(f"Health check failed: {e}")

                # Attempt reconnection if needed
                if self._state.consecutive_failures <= self.max_reconnect_attempts:
                    try:
                        async with self._reconnect_lock:
                            await self._create_pool()
                            logger.info("Successfully reconnected to Redis")
                            continue
                    except Exception as re:
                        logger.error(f"Reconnection attempt failed: {re}")

            await asyncio.sleep(self.health_check_interval)

    async def cleanup(self) -> None:
        """Cleanup all connections with improved error handling."""
        if not self._running:
            logger.info("Connection manager already cleaned up")
            return

        logger.info("Starting connection manager cleanup")
        self._running = False

        try:
            # 1. Cancel all background tasks first
            logger.info("Cancelling background tasks")
            await self._task_manager.cancel_all()

            # 2. Clean up PubSub connections
            async with self._cleanup_lock:
                pubsub_count = len(self._pubsubs)
                if pubsub_count > 0:
                    logger.info(f"Cleaning up {pubsub_count} PubSub connections")
                    cleanup_tasks = []

                    for channel, pubsub in list(self._pubsubs.items()):

                        async def cleanup_pubsub(ch: str, pb: PubSub):
                            try:
                                await asyncio.wait_for(pb.unsubscribe(ch), timeout=1.0)
                                await pb.aclose()
                                logger.info(f"Closed PubSub for channel {ch}")
                            except Exception as e:
                                logger.error(f"PubSub cleanup failed for {ch}: {e}")

                        task = self._task_manager.create_task(
                            f"cleanup_pubsub_{channel}",
                            cleanup_pubsub(channel, pubsub),
                            timeout=2.0,
                        )
                        cleanup_tasks.append(task)

                    if cleanup_tasks:
                        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                    self._pubsubs.clear()

            # 3. Clean up connection pool
            if self._pool:
                logger.info("Disconnecting connection pool")
                try:
                    await asyncio.wait_for(self._pool.disconnect(), timeout=2.0)
                except Exception as e:
                    logger.error(f"Pool disconnection failed: {e}")
                finally:
                    self._pool = None

            # 4. Reset state
            logger.info("Resetting connection manager state")
            self._state = ConnectionState()

        except Exception as e:
            logger.error(f"Connection manager cleanup failed: {e}")
            raise
        finally:
            logger.info("Connection manager cleanup completed")

    @property
    def is_healthy(self) -> bool:
        """Check if the connection is currently healthy."""
        return self._state.is_healthy and self._running

    @property
    def last_error(self) -> Optional[str]:
        """Get the last error message."""
        return self._state.last_error
