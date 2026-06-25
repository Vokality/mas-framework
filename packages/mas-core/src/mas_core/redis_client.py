"""Helpers for constructing typed Redis clients."""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis_client(
    *,
    url: str,
    socket_timeout: float | None = None,
) -> Redis[str]:
    """
    Build a Redis client for string-decoded MAS data.

    Only options used by MAS are exposed here so the connection contract remains
    explicit.
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=socket_timeout,
    )
