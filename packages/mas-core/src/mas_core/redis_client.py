"""Helpers for constructing typed Redis clients."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.asyncio.connection import (
    BlockingConnectionPool,
    SSLConnection,
    parse_url,
)
from redis.asyncio.sentinel import (
    Sentinel,
    SentinelConnectionPool,
    SentinelManagedSSLConnection,
)


@dataclass(frozen=True, slots=True)
class RedisPoolSettings:
    """Bound sockets and await capacity for a finite acquisition interval."""

    max_connections: int = 512
    acquire_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Require finite backpressure without an unbounded socket budget."""
        if not 1 <= self.max_connections <= 65_536:
            raise ValueError("Redis max_connections must be between 1 and 65536")
        if (
            not math.isfinite(self.acquire_timeout_seconds)
            or self.acquire_timeout_seconds <= 0
        ):
            raise ValueError(
                "Redis acquire_timeout_seconds must be finite and positive"
            )


_POOL_SETTINGS = TypeAdapter(RedisPoolSettings)


@dataclass(frozen=True, slots=True)
class SentinelSettings:
    """Explicit primary discovery through Redis Sentinel."""

    service_name: str
    addresses: tuple[tuple[str, int], ...]
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    sentinel_username: str | None = None
    sentinel_password: str | None = field(default=None, repr=False)
    tls: bool = False
    ca_cert_path: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate endpoint discovery and certificate pairing."""
        if not self.service_name or not self.addresses:
            raise ValueError("Sentinel requires a service_name and addresses")
        if any(not host or not 1 <= port <= 65535 for host, port in self.addresses):
            raise ValueError("Sentinel addresses require a host and valid port")
        if bool(self.client_cert_path) != bool(self.client_key_path):
            raise ValueError("Sentinel client certificate and key must be paired")
        if not self.tls and any(
            (self.ca_cert_path, self.client_cert_path, self.client_key_path)
        ):
            raise ValueError("Sentinel certificate paths require TLS")


class _AsyncSSLConnection(SSLConnection):
    """Load redis-py's cached TLS context without blocking the event loop."""

    async def _connect(self) -> None:
        async with asyncio.timeout(self.socket_connect_timeout):
            if self.ssl_context.context is None:
                await asyncio.to_thread(self.ssl_context.get)
            await super()._connect()


class _AsyncSentinelSSLConnection(SentinelManagedSSLConnection):
    """Preserve Sentinel discovery while loading TLS credentials off the loop."""

    async def _connect(self) -> None:
        async with asyncio.timeout(self.socket_connect_timeout):
            if self.ssl_context.context is None:
                await asyncio.to_thread(self.ssl_context.get)
            await super()._connect()


class _SentinelBlockingConnectionPool(SentinelConnectionPool, BlockingConnectionPool):
    """Combine native Sentinel discovery with native async capacity waiting."""


class _SentinelRedis(Redis):
    """Own both primary and discovery pools for deterministic teardown."""

    def __init__(
        self, *, sentinel: Sentinel, connection_pool: SentinelConnectionPool
    ) -> None:
        self._sentinel = sentinel
        super().__init__(connection_pool=connection_pool)

    def client(self) -> Redis:
        """Borrow one connection without transferring pool ownership."""
        return Redis(
            connection_pool=self.connection_pool, single_connection_client=True
        )

    async def aclose(self, close_connection_pool: bool | None = None) -> None:
        """Close all resources created by the Sentinel connection factory."""
        try:
            await super().aclose(
                close_connection_pool=(
                    True if close_connection_pool is None else close_connection_pool
                )
            )
        finally:
            await asyncio.gather(
                *(client.aclose() for client in self._sentinel.sentinels)
            )


def create_redis_client(
    *,
    url: str | None,
    socket_timeout: float | None = None,
    sentinel: SentinelSettings | None = None,
    pool: RedisPoolSettings = RedisPoolSettings(),
) -> Redis:
    """
    Build a Redis client for string-decoded MAS data.

    Only options used by MAS are exposed here so the connection contract remains
    explicit.
    """
    if sentinel is not None:
        if url is not None:
            raise ValueError("Choose a Redis URL or Sentinel, not both")
        discovery = Sentinel(
            sentinel.addresses,
            sentinel_kwargs={
                "username": sentinel.sentinel_username,
                "password": sentinel.sentinel_password,
                "socket_timeout": socket_timeout or 5.0,
                "socket_connect_timeout": 5.0,
                "ssl": sentinel.tls,
                "ssl_ca_certs": sentinel.ca_cert_path,
                "ssl_certfile": sentinel.client_cert_path,
                "ssl_keyfile": sentinel.client_key_path,
            },
        )
        discovery.sentinels = [
            Redis.from_pool(
                BlockingConnectionPool(
                    max_connections=pool.max_connections,
                    timeout=pool.acquire_timeout_seconds,
                    connection_class=(
                        _AsyncSSLConnection
                        if sentinel.tls
                        else client.connection_pool.connection_class
                    ),
                    **client.connection_pool.connection_kwargs,
                )
            )
            for client in discovery.sentinels
        ]
        tls_options: dict[str, object] = {}
        if sentinel.tls:
            tls_options = {
                "connection_class": _AsyncSentinelSSLConnection,
                "ssl_ca_certs": sentinel.ca_cert_path,
                "ssl_certfile": sentinel.client_cert_path,
                "ssl_keyfile": sentinel.client_key_path,
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
            }
        primary_pool = _SentinelBlockingConnectionPool(
            sentinel.service_name,
            discovery,
            max_connections=pool.max_connections,
            timeout=pool.acquire_timeout_seconds,
            username=sentinel.username,
            password=sentinel.password,
            decode_responses=True,
            protocol=2,
            socket_timeout=socket_timeout,
            socket_connect_timeout=5.0,
            ssl=sentinel.tls,
            **tls_options,
        )
        return _SentinelRedis(sentinel=discovery, connection_pool=primary_pool)
    if url is None:
        raise ValueError("A Redis URL or Sentinel configuration is required")
    url_options = parse_url(url)
    effective_pool = _POOL_SETTINGS.validate_python(
        {
            "max_connections": url_options.get("max_connections", pool.max_connections),
            "acquire_timeout_seconds": url_options.get(
                "timeout", pool.acquire_timeout_seconds
            ),
        }
    )
    connection_pool = BlockingConnectionPool.from_url(
        url,
        max_connections=effective_pool.max_connections,
        timeout=effective_pool.acquire_timeout_seconds,
        decode_responses=True,
        protocol=2,
        socket_timeout=socket_timeout,
        socket_connect_timeout=5.0,
    )
    connection_pool.timeout = effective_pool.acquire_timeout_seconds
    if connection_pool.connection_class is SSLConnection:
        connection_pool.connection_class = _AsyncSSLConnection
    return Redis.from_pool(connection_pool)
