"""Atomic per-agent sliding-window rate limiting."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError
from redis.typing import EncodableT, FieldT

logger = logging.getLogger(__name__)


class RateLimits(TypedDict):
    """Nonnegative message limits for the supported windows."""

    per_minute: Annotated[int, Field(ge=0)]
    per_hour: Annotated[int, Field(ge=0)]


_LIMITS_ADAPTER = TypeAdapter(RateLimits)
_SCRIPT_RESULT_ADAPTER = TypeAdapter(
    tuple[Literal[0, 1], int, int, float, Literal["minute", "hour"]]
)
_USAGE_ADAPTER = TypeAdapter(tuple[int, int])
_SINGLE_RESULT_ADAPTER = TypeAdapter(tuple[object])

_RATE_LIMIT_SCRIPT = """
local minute_key, hour_key, limits_key = KEYS[1], KEYS[2], KEYS[3]
local request_id, now = ARGV[1], tonumber(ARGV[2])
for index, key in ipairs(KEYS) do
    local kind = redis.call('TYPE', key).ok
    local expected = index == 3 and 'hash' or 'zset'
    if kind ~= 'none' and kind ~= expected then
        return redis.error_reply('invalid_rate_limit_storage_type')
    end
end
local minute_value = redis.call('HGET', limits_key, 'per_minute')
local hour_value = redis.call('HGET', limits_key, 'per_hour')
local per_minute = tonumber(minute_value == false and ARGV[3] or minute_value)
local per_hour = tonumber(hour_value == false and ARGV[4] or hour_value)
if not per_minute or not per_hour or per_minute < 0 or per_hour < 0
    or per_minute % 1 ~= 0 or per_hour % 1 ~= 0 then
    return redis.error_reply('Rate limits must be nonnegative integers')
end

redis.call('ZREMRANGEBYSCORE', minute_key, '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', hour_key, '-inf', now - 3600)
local minute_count = redis.call('ZCARD', minute_key)
local hour_count = redis.call('ZCARD', hour_key)

local function reset_time(key, window)
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if #oldest > 0 then
        return tostring(tonumber(oldest[2]) + window)
    end
    return tostring(now + window)
end

if minute_count >= per_minute then
    return {0, per_minute, 0, reset_time(minute_key, 60), 'minute'}
end
if hour_count >= per_hour then
    return {0, per_hour, 0, reset_time(hour_key, 3600), 'hour'}
end

redis.call('ZADD', minute_key, now, request_id)
redis.call('EXPIRE', minute_key, 60)
redis.call('ZADD', hour_key, now, request_id)
redis.call('EXPIRE', hour_key, 3600)
local minute_remaining = per_minute - minute_count - 1
local hour_remaining = per_hour - hour_count - 1
if hour_remaining < minute_remaining then
    return {1, per_hour, hour_remaining, reset_time(hour_key, 3600), 'hour'}
end
return {1, per_minute, minute_remaining, reset_time(minute_key, 60), 'minute'}
"""


class RateLimitResult(BaseModel):
    """Admission status and the remaining quota of its limiting window."""

    allowed: bool
    limit: int = Field(ge=0)
    remaining: int = Field(ge=0)
    reset_time: float = Field(allow_inf_nan=False)
    window: Literal["minute", "hour"] = "minute"


class RateLimitModule:
    """Enforce both sliding windows atomically using Redis sorted sets."""

    def __init__(
        self,
        redis: Redis,
        default_per_minute: int,
        default_per_hour: int,
    ) -> None:
        defaults = _LIMITS_ADAPTER.validate_python(
            {"per_minute": default_per_minute, "per_hour": default_per_hour}
        )
        self.redis = redis
        self.default_per_minute = defaults["per_minute"]
        self.default_per_hour = defaults["per_hour"]

    async def check_rate_limit(self, agent_id: str, message_id: str) -> RateLimitResult:
        """Count every admitted request, including repeated message IDs."""
        async with self.redis.pipeline(transaction=False) as pipeline:
            self.queue_check(pipeline, agent_id, message_id)
            (raw,) = _SINGLE_RESULT_ADAPTER.validate_python(await pipeline.execute())
        result = self.parse_result(raw)
        if not result.allowed:
            logger.warning(
                "Rate limit exceeded (per-%s)",
                result.window,
                extra={
                    "agent_id": agent_id,
                    "limit": result.limit,
                    "remaining": result.remaining,
                },
            )
        return result

    def queue_check(self, pipeline: Pipeline, agent_id: str, message_id: str) -> None:
        """Queue atomic admission alongside other checks on an unwatched pipeline."""
        if pipeline.watching:
            raise ValueError("admission checks require an unwatched pipeline")
        pipeline.eval(
            _RATE_LIMIT_SCRIPT,
            3,
            f"ratelimit:{agent_id}:minute",
            f"ratelimit:{agent_id}:hour",
            f"ratelimit:{agent_id}:limits",
            f"{message_id}:{uuid4()}",
            str(time.time()),
            str(self.default_per_minute),
            str(self.default_per_hour),
        )

    @staticmethod
    def parse_result(raw: object) -> RateLimitResult:
        """Validate one Redis admission result, preserving storage failures."""
        if isinstance(raw, RedisError):
            raise raw
        allowed, limit, remaining, reset_time, window = (
            _SCRIPT_RESULT_ADAPTER.validate_python(raw)
        )
        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=remaining,
            reset_time=reset_time,
            window=window,
        )

    async def check_rate_limit_legacy(
        self, agent_id: str, message_id: str
    ) -> RateLimitResult:
        """Compatibility entrypoint sharing the atomic admission implementation."""
        return await self.check_rate_limit(agent_id, message_id)

    async def set_limits(
        self,
        agent_id: str,
        per_minute: int | None = None,
        per_hour: int | None = None,
    ) -> None:
        """Validate all custom values before updating the agent's configuration."""
        limits = _LIMITS_ADAPTER.validate_python(
            {
                "per_minute": self.default_per_minute
                if per_minute is None
                else per_minute,
                "per_hour": self.default_per_hour if per_hour is None else per_hour,
            }
        )
        fields: dict[FieldT, EncodableT] = {}
        if per_minute is not None:
            fields["per_minute"] = str(limits["per_minute"])
        if per_hour is not None:
            fields["per_hour"] = str(limits["per_hour"])
        if fields:
            await self.redis.hset(f"ratelimit:{agent_id}:limits", mapping=fields)

    async def get_limits(self, agent_id: str) -> RateLimits:
        """Read and validate configured limits, filling missing fields from defaults."""
        raw = await self.redis.hgetall(f"ratelimit:{agent_id}:limits")
        return _LIMITS_ADAPTER.validate_python(
            {
                "per_minute": raw.get("per_minute", self.default_per_minute),
                "per_hour": raw.get("per_hour", self.default_per_hour),
            }
        )

    async def reset_limits(self, agent_id: str) -> None:
        """Clear the exact two counters, preserving custom limits."""
        await self.redis.delete(
            f"ratelimit:{agent_id}:minute", f"ratelimit:{agent_id}:hour"
        )

    async def get_current_usage(self, agent_id: str) -> RateLimits:
        """Read live usage without mutating the counters."""
        now = time.time()
        async with self.redis.pipeline() as pipe:
            pipe.zcount(f"ratelimit:{agent_id}:minute", f"({now - 60}", "+inf")
            pipe.zcount(f"ratelimit:{agent_id}:hour", f"({now - 3600}", "+inf")
            raw: object = await pipe.execute()
        minute_count, hour_count = _USAGE_ADAPTER.validate_python(raw)
        return {"per_minute": minute_count, "per_hour": hour_count}
