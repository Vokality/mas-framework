"""Typed protocol for the asyncio Redis client used in MAS.

This narrows the subset of methods we rely on with precise async
signatures, avoiding the need for "Any" while remaining compatible
with redis.asyncio.client.Redis via structural typing.
"""

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Literal,
    MutableMapping,
    Protocol,
    Self,
    Set,
    overload,
)


class PubSubProtocol(Protocol):
    """Protocol for Redis Pub/Sub interfaces."""

    async def subscribe(self, *channels: str) -> None:
        """Subscribe to one or more channels."""
        ...

    async def unsubscribe(self, *channels: str) -> None:
        """Unsubscribe from one or more channels."""
        ...

    async def aclose(self) -> None:
        """Close the Pub/Sub connection."""
        ...

    def listen(self) -> AsyncIterator[MutableMapping[str, Any]]:
        """Yield Pub/Sub messages as they arrive."""
        ...


class PipelineProtocol(Protocol):
    """
    Protocol for Redis pipeline (batched commands).

    Pipelines allow multiple commands to be sent in a single round-trip,
    significantly reducing latency for bulk operations.
    """

    # Pipeline returns self for chaining
    def hgetall(self, key: str) -> Self:
        """Queue HGETALL for the hash key."""
        ...

    def hset(self, key: str, *, mapping: dict[str, str]) -> Self:
        """Queue HSET for multiple hash fields."""
        ...

    def hget(self, key: str, field: str) -> Self:
        """Queue HGET for a hash field."""
        ...

    def delete(self, *keys: str) -> Self:
        """Queue DEL for one or more keys."""
        ...

    def exists(self, key: str) -> Self:
        """Queue EXISTS for a key."""
        ...

    def get(self, key: str) -> Self:
        """Queue GET for a key."""
        ...

    def set(self, key: str, value: str) -> Self:
        """Queue SET for a key."""
        ...

    def xadd(self, name: str, fields: dict[str, str]) -> Self:
        """Queue XADD for a stream entry."""
        ...

    def zadd(self, key: str, mapping: dict[str, float]) -> Self:
        """Queue ZADD for a sorted set."""
        ...

    def zcard(self, key: str) -> Self:
        """Queue ZCARD for a sorted set."""
        ...

    def expire(self, key: str, seconds: int) -> Self:
        """Queue EXPIRE for a key."""
        ...

    def ttl(self, key: str) -> Self:
        """Queue TTL for a key."""
        ...

    def setex(self, key: str, seconds: int, value: str) -> Self:
        """Queue SETEX for a key."""
        ...

    def zcount(self, key: str, min: float | str, max: float | str) -> Self:
        """Queue ZCOUNT for a score range."""
        ...

    def incr(self, key: str) -> Self:
        """Queue INCR for a key."""
        ...

    def decr(self, key: str) -> Self:
        """Queue DECR for a key."""
        ...

    # Execute all queued commands and return results
    async def execute(self) -> list[Any]:
        """Execute queued pipeline commands."""
        ...


class AsyncRedisProtocol(Protocol):
    """Protocol for Redis async client methods used by MAS."""

    # Connection
    def aclose(self) -> Awaitable[None]:
        """Close the client connection."""
        ...

    # Keys
    def exists(self, key: str) -> Awaitable[int]:
        """Return whether a key exists."""
        ...

    def delete(self, *keys: str) -> Awaitable[int]:
        """Delete one or more keys."""
        ...

    def expire(self, key: str, seconds: int) -> Awaitable[int]:
        """Set key expiration in seconds."""
        ...

    def ttl(self, key: str) -> Awaitable[int]:
        """Return key time-to-live in seconds."""
        ...

    def scan(
        self, cursor: int, *, match: str, count: int
    ) -> Awaitable[tuple[int, list[str]]]:
        """Scan keys matching a pattern."""
        ...

    def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        """Iterate keys matching a pattern."""
        ...

    # Strings
    def get(self, key: str) -> Awaitable[str | None]:
        """Get a string value by key."""
        ...

    def set(self, key: str, value: str) -> Awaitable[bool | str]:
        """Set a string value for a key."""
        ...

    def setex(self, key: str, seconds: int, value: str) -> Awaitable[bool | str]:
        """Set a value with expiration in seconds."""
        ...

    def incr(self, key: str) -> Awaitable[int]:
        """Increment a numeric key."""
        ...

    def decr(self, key: str) -> Awaitable[int]:
        """Decrement a numeric key."""
        ...

    def publish(self, channel: str, message: str) -> Awaitable[int]:
        """Publish a message to a channel."""
        ...

    def pubsub(self) -> PubSubProtocol:
        """Create a Pub/Sub interface."""
        ...

    # Hashes
    def hget(self, key: str, field: str) -> Awaitable[str | None]:
        """Get a field from a hash."""
        ...

    def hset(self, key: str, *, mapping: dict[str, str]) -> Awaitable[int]:
        """Set one or more fields in a hash."""
        ...

    def hgetall(self, key: str) -> Awaitable[dict[str, str]]:
        """Get all fields and values from a hash."""
        ...

    def hdel(self, key: str, *fields: str) -> Awaitable[int]:
        """Delete one or more fields from a hash."""
        ...

    # Sets
    def sadd(self, key: str, *members: str) -> Awaitable[int]:
        """Add members to a set."""
        ...

    def srem(self, key: str, *members: str) -> Awaitable[int]:
        """Remove members from a set."""
        ...

    def smembers(self, key: str) -> Awaitable[Set[str]]:
        """Return all members of a set."""
        ...

    def sismember(self, key: str, member: str) -> Awaitable[bool]:
        """Check if a member is in a set."""
        ...

    # Sorted sets
    def zadd(self, key: str, mapping: dict[str, float]) -> Awaitable[int]:
        """Add members to a sorted set."""
        ...

    def zcard(self, key: str) -> Awaitable[int]:
        """Return the cardinality of a sorted set."""
        ...

    def zrem(self, key: str, *members: str) -> Awaitable[int]:
        """Remove members from a sorted set."""
        ...

    def zremrangebyscore(
        self, key: str, min: float | str, max: float | str
    ) -> Awaitable[int]:
        """Remove members in a score range."""
        ...

    def zcount(self, key: str, min: float | str, max: float | str) -> Awaitable[int]:
        """Count members in a score range."""
        ...

    def zscore(self, key: str, member: str) -> Awaitable[float | None]:
        """Get the score for a member."""
        ...

    @overload
    def zrange(
        self, key: str, start: int, end: int, *, withscores: Literal[True]
    ) -> Awaitable[list[tuple[str, float]]]:
        """Get a range of members with scores."""
        ...

    @overload
    def zrange(
        self, key: str, start: int, end: int, *, withscores: Literal[False] = ...
    ) -> Awaitable[list[str]]:
        """Get a range of members without scores."""
        ...

    # Streams
    def xadd(self, name: str, fields: dict[str, str]) -> Awaitable[str]:
        """Add an entry to a stream."""
        ...

    def xread(
        self,
        streams: dict[str, str],
        *,
        count: int | None = ...,
        block: int | None = ...,
    ) -> Awaitable[list[tuple[str, list[tuple[str, dict[str, str]]]]] | None]:
        """Read stream entries with optional blocking."""
        ...

    def xrange(
        self, name: str, min: str, max: str, count: int | None = ...
    ) -> Awaitable[list[tuple[str, dict[str, str]]]]:
        """Read a stream range in forward order."""
        ...

    def xrevrange(
        self, name: str, max: str, min: str, count: int | None = ...
    ) -> Awaitable[list[tuple[str, dict[str, str]]]]:
        """Read a stream range in reverse order."""
        ...

    def xlen(self, name: str) -> Awaitable[int]:
        """Return the length of a stream."""
        ...

    def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = ...
    ) -> Awaitable[str]:
        """Create a consumer group for a stream."""
        ...

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        *,
        streams: dict[str, str],
        count: int | None = ...,
        block: int | None = ...,
    ) -> Awaitable[list[tuple[str, list[tuple[str, dict[str, str]]]]] | None]:
        """Read stream entries from a consumer group."""
        ...

    def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        *,
        count: int | None = ...,
    ) -> Awaitable[tuple[str, list[tuple[str, dict[str, str]]], list[str]]]:
        """Claim idle messages for a consumer group."""
        ...

    def xack(self, name: str, groupname: str, *ids: str) -> Awaitable[int]:
        """Acknowledge processed stream entries."""
        ...

    # Scripting
    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str,
    ) -> Awaitable[Any]:
        """Evaluate a Lua script."""
        ...

    # Pipeline
    def pipeline(self) -> PipelineProtocol:
        """Create a pipeline for batched commands."""
        ...
