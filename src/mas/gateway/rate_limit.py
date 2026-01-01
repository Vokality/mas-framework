"""Rate Limiting Module for Gateway Service."""

import logging
import time
from typing import Optional

from pydantic import BaseModel

from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


# Lua script for atomic rate limit check
# This reduces 10 Redis calls to 1 atomic operation
_RATE_LIMIT_SCRIPT = """
-- KEYS[1] = minute window key (ratelimit:{agent_id}:minute)
-- KEYS[2] = hour window key (ratelimit:{agent_id}:hour)
-- KEYS[3] = limits key (ratelimit:{agent_id}:limits)
-- ARGV[1] = message_id
-- ARGV[2] = current timestamp (float)
-- ARGV[3] = default per_minute limit
-- ARGV[4] = default per_hour limit

local minute_key = KEYS[1]
local hour_key = KEYS[2]
local limits_key = KEYS[3]
local message_id = ARGV[1]
local now = tonumber(ARGV[2])
local default_per_minute = tonumber(ARGV[3])
local default_per_hour = tonumber(ARGV[4])

-- Get custom limits or use defaults
local per_minute = tonumber(redis.call('HGET', limits_key, 'per_minute')) or default_per_minute
local per_hour = tonumber(redis.call('HGET', limits_key, 'per_hour')) or default_per_hour

-- Check minute window
local minute_start = now - 60
redis.call('ZREMRANGEBYSCORE', minute_key, '-inf', minute_start)
local minute_count = redis.call('ZCARD', minute_key)

if minute_count >= per_minute then
    -- Rate limited by minute
    return {0, per_minute, 0, now + 60, 'minute'}
end

-- Check hour window
local hour_start = now - 3600
redis.call('ZREMRANGEBYSCORE', hour_key, '-inf', hour_start)
local hour_count = redis.call('ZCARD', hour_key)

if hour_count >= per_hour then
    -- Rate limited by hour
    return {0, per_hour, 0, now + 3600, 'hour'}
end

-- Allowed - add to both windows
redis.call('ZADD', minute_key, now, message_id)
redis.call('EXPIRE', minute_key, 60)
redis.call('ZADD', hour_key, now, message_id)
redis.call('EXPIRE', hour_key, 3600)

-- Return success with minute window info (more restrictive)
local remaining = per_minute - minute_count - 1
return {1, per_minute, remaining, now + 60, 'minute'}
"""


class RateLimitResult(BaseModel):
    """Rate limit check result."""

    allowed: bool
    limit: int
    remaining: int
    reset_time: float


class RateLimitModule:
    """
    Rate limiting module using token bucket algorithm.

    Implements per-agent rate limiting as per GATEWAY.md:
    - Token bucket algorithm
    - Configurable limits (messages per minute, per hour)
    - Burst tolerance
    - Sliding window implementation using Redis sorted sets

    Redis Data Model:
        ratelimit:{agent_id}:{window} → Sorted set of timestamps
            Score: timestamp, Value: message_id
            TTL: window size
    """

    def __init__(
        self,
        redis: AsyncRedisProtocol,
        default_per_minute: int = 100,
        default_per_hour: int = 1000,
    ):
        """
        Initialize rate limiting module.

        Args:
            redis: Redis connection
            default_per_minute: Default messages per minute
            default_per_hour: Default messages per hour
        """
        self.redis: AsyncRedisProtocol = redis
        self.default_per_minute = default_per_minute
        self.default_per_hour = default_per_hour

    async def check_rate_limit(self, agent_id: str, message_id: str) -> RateLimitResult:
        """
        Check if agent is within rate limits.

        Uses sliding window algorithm with two windows:
        - 60 seconds (per-minute limit)
        - 3600 seconds (per-hour limit)

        This method uses a Lua script to perform all operations atomically
        in a single Redis round-trip, reducing calls from ~10 to 1.

        Args:
            agent_id: Agent identifier
            message_id: Message identifier

        Returns:
            RateLimitResult with limit status
        """
        now = time.time()

        minute_key = f"ratelimit:{agent_id}:minute"
        hour_key = f"ratelimit:{agent_id}:hour"
        limits_key = f"ratelimit:{agent_id}:limits"

        # Execute Lua script atomically
        result = await self.redis.eval(
            _RATE_LIMIT_SCRIPT,
            3,  # number of keys
            minute_key,
            hour_key,
            limits_key,
            message_id,
            str(now),
            str(self.default_per_minute),
            str(self.default_per_hour),
        )

        # Parse result: [allowed, limit, remaining, reset_time, window]
        allowed = bool(result[0])
        limit = int(result[1])
        remaining = int(result[2])
        reset_time = float(result[3])
        window = result[4] if len(result) > 4 else "minute"

        if not allowed:
            logger.warning(
                f"Rate limit exceeded (per-{window})",
                extra={
                    "agent_id": agent_id,
                    "limit": limit,
                    "remaining": remaining,
                },
            )
        else:
            logger.debug(
                "Rate limit check passed",
                extra={
                    "agent_id": agent_id,
                    "remaining": remaining,
                },
            )

        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_time=reset_time,
        )

    async def check_rate_limit_legacy(
        self, agent_id: str, message_id: str
    ) -> RateLimitResult:
        """
        Legacy rate limit check without Lua script.

        Kept for compatibility with Redis servers that don't support Lua.
        Uses multiple Redis calls (~10 per check).

        Args:
            agent_id: Agent identifier
            message_id: Message identifier

        Returns:
            RateLimitResult with limit status
        """
        now = time.time()

        # Get agent-specific limits
        limits = await self.get_limits(agent_id)

        # Check minute limit
        minute_result = await self._check_window(
            agent_id,
            message_id,
            window="minute",
            window_seconds=60,
            limit=limits["per_minute"],
            now=now,
        )

        if not minute_result.allowed:
            logger.warning(
                "Rate limit exceeded (per-minute)",
                extra={
                    "agent_id": agent_id,
                    "limit": minute_result.limit,
                    "remaining": minute_result.remaining,
                },
            )
            return minute_result

        # Check hour limit
        hour_result = await self._check_window(
            agent_id,
            message_id,
            window="hour",
            window_seconds=3600,
            limit=limits["per_hour"],
            now=now,
        )

        if not hour_result.allowed:
            logger.warning(
                "Rate limit exceeded (per-hour)",
                extra={
                    "agent_id": agent_id,
                    "limit": hour_result.limit,
                    "remaining": hour_result.remaining,
                },
            )
            return hour_result

        logger.debug(
            "Rate limit check passed",
            extra={
                "agent_id": agent_id,
                "minute_remaining": minute_result.remaining,
                "hour_remaining": hour_result.remaining,
            },
        )

        # Return the more restrictive result
        return minute_result

    async def _check_window(
        self,
        agent_id: str,
        message_id: str,
        window: str,
        window_seconds: int,
        limit: int,
        now: float,
    ) -> RateLimitResult:
        """
        Check rate limit for a specific time window.

        Args:
            agent_id: Agent identifier
            message_id: Message identifier
            window: Window name (for key)
            window_seconds: Window size in seconds
            limit: Maximum messages in window
            now: Current timestamp

        Returns:
            RateLimitResult
        """
        key = f"ratelimit:{agent_id}:{window}"
        window_start = now - window_seconds

        # Remove expired entries (outside window)
        await self.redis.zremrangebyscore(key, "-inf", window_start)

        # Count current messages in window
        count = await self.redis.zcard(key)

        # Check if limit exceeded
        if count >= limit:
            reset_time = now + window_seconds
            return RateLimitResult(
                allowed=False, limit=limit, remaining=0, reset_time=reset_time
            )

        # Add current message to window
        await self.redis.zadd(key, {message_id: now})

        # Set TTL on key
        await self.redis.expire(key, window_seconds)

        remaining = limit - count - 1

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=remaining,
            reset_time=now + window_seconds,
        )

    async def set_limits(
        self,
        agent_id: str,
        per_minute: Optional[int] = None,
        per_hour: Optional[int] = None,
    ) -> None:
        """
        Set custom rate limits for an agent.

        Args:
            agent_id: Agent identifier
            per_minute: Messages per minute (None = use default)
            per_hour: Messages per hour (None = use default)
        """
        limits_key = f"ratelimit:{agent_id}:limits"

        if per_minute is not None:
            await self.redis.hset(limits_key, mapping={"per_minute": str(per_minute)})

        if per_hour is not None:
            await self.redis.hset(limits_key, mapping={"per_hour": str(per_hour)})

        logger.info(
            "Rate limits updated",
            extra={
                "agent_id": agent_id,
                "per_minute": per_minute,
                "per_hour": per_hour,
            },
        )

    async def get_limits(self, agent_id: str) -> dict[str, int]:
        """
        Get rate limits for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Dictionary with per_minute and per_hour limits
        """
        limits_key = f"ratelimit:{agent_id}:limits"

        per_minute_str = await self.redis.hget(limits_key, "per_minute")
        per_hour_str = await self.redis.hget(limits_key, "per_hour")

        per_minute = int(per_minute_str) if per_minute_str else self.default_per_minute
        per_hour = int(per_hour_str) if per_hour_str else self.default_per_hour

        return {"per_minute": per_minute, "per_hour": per_hour}

    async def reset_limits(self, agent_id: str) -> None:
        """
        Reset rate limits for an agent (clear counters).

        Args:
            agent_id: Agent identifier
        """
        # Delete all rate limit windows for agent
        pattern = f"ratelimit:{agent_id}:*"
        keys: list[str] = []
        async for key in self.redis.scan_iter(match=pattern):
            # Don't delete the limits config, only counters
            if not key.endswith(":limits"):
                keys.append(key)

        if keys:
            await self.redis.delete(*keys)

        logger.info("Rate limits reset", extra={"agent_id": agent_id})

    async def get_current_usage(self, agent_id: str) -> dict[str, int]:
        """
        Get current usage for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Dictionary with current usage counts
        """
        now = time.time()

        # Count messages in minute window
        minute_key = f"ratelimit:{agent_id}:minute"
        minute_start = now - 60
        await self.redis.zremrangebyscore(minute_key, "-inf", minute_start)
        minute_count = await self.redis.zcard(minute_key)

        # Count messages in hour window
        hour_key = f"ratelimit:{agent_id}:hour"
        hour_start = now - 3600
        await self.redis.zremrangebyscore(hour_key, "-inf", hour_start)
        hour_count = await self.redis.zcard(hour_key)

        return {"per_minute": minute_count, "per_hour": hour_count}
