"""Validated stream effects that can participate in an atomic Redis commit."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio.connection import ConnectionPool

from .durability import RedisDurability


@dataclass(frozen=True, slots=True)
class RedisCommitTarget:
    """The storage and confirmation policy that owns an atomic Redis commit."""

    connection_pool: ConnectionPool
    durability: RedisDurability

    def compatible_with(self, other: RedisCommitTarget) -> bool:
        """Combine writes only when their backend and policy are explicitly shared.

        Equal endpoint strings or settings do not establish ownership of another
        module's connection or confirmation behavior.
        """
        return (
            self.connection_pool is other.connection_pool
            and self.durability is other.durability
        )


@dataclass(frozen=True, slots=True)
class StreamAppend:
    """An immutable stream append intent, independent of the committing module."""

    stream: str
    fields: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Reject malformed effects before they enter Redis commit logic."""
        if not isinstance(self.stream, str) or not self.stream:
            raise ValueError("stream must be a nonempty string")
        if not isinstance(self.fields, tuple) or not 0 < len(self.fields) <= 1024:
            raise ValueError("fields must contain between 1 and 1024 field pairs")
        names: set[str] = set()
        for pair in self.fields:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not pair[0]
                or not isinstance(pair[1], str)
                or pair[0] in names
            ):
                raise ValueError(
                    "fields require distinct nonempty names and string values"
                )
            names.add(pair[0])
