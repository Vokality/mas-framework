"""Corrupt rate-limit storage must not grant quota or partially change counters."""

from __future__ import annotations

import time

import pytest
from mas_gateway.rate_limit import RateLimitModule
from redis.asyncio import Redis
from redis.exceptions import ResponseError

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("field", ["per_minute", "per_hour"])
@pytest.mark.parametrize("value", ["nonsense", "", "-1", "1.5", "nan", "inf", "1e999"])
async def test_malformed_stored_quota_fails_before_counter_mutation(
    redis: Redis, field: str, value: str
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    minute, hour = "ratelimit:sender:minute", "ratelimit:sender:hour"
    await redis.zadd(minute, {"expired": time.time() - 120})
    await redis.zadd(hour, {"expired": time.time() - 7200})
    await redis.hset("ratelimit:sender:limits", mapping={field: value})
    before = await redis.dump(minute), await redis.dump(hour)
    with pytest.raises(ResponseError, match="Rate limits must be nonnegative integers"):
        await limiter.check_rate_limit("sender", "attempt")
    assert (await redis.dump(minute), await redis.dump(hour)) == before


@pytest.mark.parametrize("corrupt", ["minute", "hour", "limits"])
async def test_all_key_types_are_checked_before_expired_counter_cleanup(
    redis: Redis, corrupt: str
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    minute, hour, limits = (
        "ratelimit:sender:minute",
        "ratelimit:sender:hour",
        "ratelimit:sender:limits",
    )
    await redis.zadd(minute, {"expired": time.time() - 120})
    await redis.zadd(hour, {"expired": time.time() - 7200})
    await redis.hset(limits, mapping={"per_minute": "5"})
    await redis.set(f"ratelimit:sender:{corrupt}", "corrupt-type")
    before = await redis.dump(minute), await redis.dump(hour), await redis.dump(limits)
    with pytest.raises(ResponseError, match="invalid_rate_limit_storage_type"):
        await limiter.check_rate_limit("sender", "attempt")
    assert (
        await redis.dump(minute),
        await redis.dump(hour),
        await redis.dump(limits),
    ) == before


@pytest.mark.parametrize(
    ("field", "value", "limit", "window"),
    [
        (None, None, 5, "minute"),
        ("per_minute", "2", 2, "minute"),
        ("per_hour", "2", 2, "hour"),
    ],
)
async def test_only_missing_quota_fields_use_defaults(
    redis: Redis, field: str | None, value: str | None, limit: int, window: str
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    if field is not None and value is not None:
        await redis.hset("ratelimit:sender:limits", mapping={field: value})
    result = await limiter.check_rate_limit("sender", "attempt")
    assert result.allowed
    assert result.limit == limit
    assert result.window == window


@pytest.mark.parametrize("field", ["per_minute", "per_hour"])
async def test_zero_stored_quota_remains_a_valid_explicit_deny(
    redis: Redis, field: str
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    await redis.hset("ratelimit:sender:limits", mapping={field: "0"})
    result = await limiter.check_rate_limit("sender", "attempt")
    assert not result.allowed
    assert result.limit == 0
    assert await limiter.get_current_usage("sender") == {"per_minute": 0, "per_hour": 0}
