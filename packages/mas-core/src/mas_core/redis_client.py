"""Helpers for constructing typed Redis clients."""

from __future__ import annotations

from typing import Any, cast

from redis.asyncio import Redis

from .redis_types import AsyncRedisProtocol


def create_redis_client(
    *,
    url: str,
    decode_responses: bool = True,
    **kwargs: Any,
) -> AsyncRedisProtocol:
    """
    Build a Redis client that satisfies AsyncRedisProtocol.

    Additional keyword arguments are passed directly to Redis.from_url so callers
    can tune connection settings (timeouts, TLS, etc.).
    """
    client = Redis.from_url(
        url,
        decode_responses=decode_responses,
        **kwargs,
    )
    return cast(AsyncRedisProtocol, client)
