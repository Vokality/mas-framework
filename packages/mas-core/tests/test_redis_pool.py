"""Bounded Redis pools await capacity without blocking or leaking sockets."""

from __future__ import annotations

import asyncio
from typing import Literal

import pytest
from mas_core.redis_client import (
    RedisPoolSettings,
    SentinelSettings,
    create_redis_client,
)
from redis.asyncio import Redis
from redis.asyncio.connection import AbstractConnection, BlockingConnectionPool
from redis.asyncio.sentinel import SentinelConnectionPool
from redis.exceptions import ConnectionError as RedisConnectionError

pytestmark = pytest.mark.asyncio

Backend = Literal["standalone", "sentinel", "discovery"]


def _client_pool(
    backend: Backend, settings: RedisPoolSettings
) -> tuple[Redis, BlockingConnectionPool]:
    owner = create_redis_client(
        url="redis://redis.test:6379/2" if backend == "standalone" else None,
        sentinel=(
            None
            if backend == "standalone"
            else SentinelSettings("primary", (("sentinel.test", 26379),))
        ),
        pool=settings,
    )
    pool = owner.connection_pool
    if backend == "discovery":
        assert isinstance(pool, SentinelConnectionPool)
        pool = pool.sentinel_manager.sentinels[0].connection_pool
    assert isinstance(pool, BlockingConnectionPool)
    return owner, pool


async def _ready(pool: BlockingConnectionPool, connection: AbstractConnection) -> None:
    """Isolate the real pool capacity algorithm from network establishment."""


@pytest.mark.parametrize("backend", ["standalone", "sentinel", "discovery"])
async def test_saturated_pool_awaits_capacity_and_keeps_heartbeat_running(
    monkeypatch: pytest.MonkeyPatch, backend: Backend
) -> None:
    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", _ready)
    owner, pool = _client_pool(backend, RedisPoolSettings(1, 0.5))
    held = await pool.get_connection()
    waiting = asyncio.create_task(pool.get_connection())
    try:
        await asyncio.sleep(0.005)
        assert not waiting.done()
        assert len(pool._in_use_connections) == 1
        assert not pool._available_connections
        await pool.release(held)
        received = await asyncio.wait_for(waiting, 0.2)
        assert received is held
        assert len(pool._in_use_connections) == 1
        await pool.release(received)
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize("backend", ["standalone", "sentinel", "discovery"])
async def test_canceling_pool_waiter_preserves_capacity(
    monkeypatch: pytest.MonkeyPatch, backend: Backend
) -> None:
    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", _ready)
    owner, pool = _client_pool(backend, RedisPoolSettings(1, 0.5))
    held = await pool.get_connection()
    waiting = asyncio.create_task(pool.get_connection())
    try:
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert pool._in_use_connections == {held}
        await pool.release(held)
        received = await pool.get_connection()
        assert received is held
        await pool.release(received)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("backend", ["standalone", "sentinel", "discovery"])
async def test_pool_acquisition_timeout_is_finite_and_reusable(
    monkeypatch: pytest.MonkeyPatch, backend: Backend
) -> None:
    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", _ready)
    owner, pool = _client_pool(backend, RedisPoolSettings(1, 0.01))
    held = await pool.get_connection()
    try:
        with pytest.raises(RedisConnectionError, match="No connection available"):
            await asyncio.wait_for(pool.get_connection(), 0.2)
        assert pool._in_use_connections == {held}
        await pool.release(held)
        received = await pool.get_connection()
        assert received is held
        await pool.release(received)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("backend", ["standalone", "sentinel", "discovery"])
async def test_default_capacity_exceeds_reproduced_redis_100_connection_limit(
    monkeypatch: pytest.MonkeyPatch, backend: Backend
) -> None:
    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", _ready)
    owner, pool = _client_pool(backend, RedisPoolSettings())
    try:
        held = await asyncio.gather(*(pool.get_connection() for _ in range(101)))
        assert pool.max_connections == 512
        assert pool.timeout == 5.0
        assert len(pool._in_use_connections) == 101
        for connection in held:
            await pool.release(connection)
    finally:
        await owner.aclose()


async def test_canceled_connection_setup_returns_reserved_pool_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    async def connecting(
        pool: BlockingConnectionPool, connection: AbstractConnection
    ) -> None:
        started.set()
        await released.wait()

    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", connecting)
    owner, pool = _client_pool("sentinel", RedisPoolSettings(1, 0.5))
    task = asyncio.create_task(pool.get_connection())
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not pool._in_use_connections
        released.set()
        connection = await pool.get_connection()
        await pool.release(connection)
    finally:
        released.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize("sentinel", [False, True])
async def test_borrowed_client_releases_without_closing_owned_pools(
    monkeypatch: pytest.MonkeyPatch, sentinel: bool
) -> None:
    monkeypatch.setattr(BlockingConnectionPool, "ensure_connection", _ready)
    disconnected: list[AbstractConnection] = []

    async def disconnect(connection: AbstractConnection, nowait: bool = False) -> None:
        disconnected.append(connection)

    monkeypatch.setattr(AbstractConnection, "disconnect", disconnect)
    owner, pool = _client_pool(
        "sentinel" if sentinel else "standalone", RedisPoolSettings(1, 0.5)
    )
    async with owner.client() as borrowed:
        assert borrowed.connection_pool is pool
        assert len(pool._in_use_connections) == 1
    assert not disconnected
    assert not pool._in_use_connections
    expected = list(pool._available_connections)
    if sentinel:
        assert isinstance(pool, SentinelConnectionPool)
        discovery_pool = pool.sentinel_manager.sentinels[0].connection_pool
        assert isinstance(discovery_pool, BlockingConnectionPool)
        discovery_connection = await discovery_pool.get_connection()
        await discovery_pool.release(discovery_connection)
        expected.append(discovery_connection)
    await owner.aclose()
    assert set(disconnected) == set(expected)


async def test_url_overrides_remain_typed_and_preserve_connection_configuration() -> (
    None
):
    owner = create_redis_client(
        url=(
            "redis://reader:password@redis.test:6379/3"
            "?max_connections=7&timeout=0.25&socket_timeout=9"
        ),
        pool=RedisPoolSettings(4, 0.5),
        socket_timeout=1,
    )
    pool = owner.connection_pool
    assert isinstance(pool, BlockingConnectionPool)
    try:
        assert pool.max_connections == 7
        assert pool.timeout == 0.25
        connection = pool.make_connection()
        assert connection.username == "reader"
        assert connection.password == "password"
        assert connection.db == 3
        assert connection.socket_timeout == 9
        assert connection.socket_connect_timeout == 5
        assert connection.encoder.decode_responses
    finally:
        await owner.aclose()


@pytest.mark.parametrize("maximum", [0, -1, 65_537])
async def test_invalid_pool_capacity_is_rejected(maximum: int) -> None:
    with pytest.raises(ValueError, match="max_connections"):
        RedisPoolSettings(max_connections=maximum)
    with pytest.raises(ValueError, match="max_connections"):
        create_redis_client(url=f"redis://redis.test?max_connections={maximum}")


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
async def test_unbounded_or_invalid_acquisition_timeout_is_rejected(
    timeout: float,
) -> None:
    with pytest.raises(ValueError, match="acquire_timeout_seconds"):
        RedisPoolSettings(acquire_timeout_seconds=timeout)
    with pytest.raises(ValueError, match="acquire_timeout_seconds"):
        create_redis_client(url=f"redis://redis.test?timeout={timeout}")
