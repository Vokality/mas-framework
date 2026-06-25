"""Helpers for constructing typed Redis clients."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis


def create_redis_client(
    *,
    url: str,
    **kwargs: Any,
) -> Redis[str]:
    """
    Build a Redis client for string-decoded MAS data.

    Additional keyword arguments are passed directly to Redis.from_url so callers
    can tune connection settings (timeouts, TLS, etc.).
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        **kwargs,
    )
