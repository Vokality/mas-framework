"""TLS credential loading stays lazy and does not block async Redis connects."""

from __future__ import annotations

import asyncio
import ssl
import threading

import pytest
from mas_core.redis_client import SentinelSettings, create_redis_client
from redis.asyncio.connection import Connection, RedisSSLContext, SSLConnection
from redis.asyncio.sentinel import SentinelConnectionPool, SentinelManagedSSLConnection


@pytest.mark.parametrize("sentinel", [False, True])
async def test_first_tls_connect_keeps_heartbeat_running_and_reuses_context(
    monkeypatch: pytest.MonkeyPatch, sentinel: bool
) -> None:
    started = threading.Event()
    released = threading.Event()
    stopped = asyncio.Event()
    loads: list[int] = []
    contexts: list[ssl.SSLContext] = []
    ticks = 0
    loop_thread = threading.get_ident()

    def slow_context(context: RedisSSLContext) -> ssl.SSLContext:
        if context.context is None:
            loads.append(threading.get_ident())
            started.set()
            if not released.wait(1):
                raise TimeoutError("test credential read did not finish")
            context.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return context.context

    async def socket_connect(connection: Connection) -> None:
        arguments = connection._connection_arguments()
        context = arguments["ssl"]
        assert isinstance(context, ssl.SSLContext)
        contexts.append(context)

    async def handshake(connection: Connection, check_health: bool = True) -> None:
        """Isolate credential loading from network authentication in this test."""

    async def primary(pool: SentinelConnectionPool) -> tuple[str, int]:
        return "redis.test", 6380

    async def heartbeat() -> None:
        nonlocal ticks
        while not stopped.is_set():
            ticks += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(RedisSSLContext, "get", slow_context)
    monkeypatch.setattr(Connection, "_connect", socket_connect)
    monkeypatch.setattr(Connection, "on_connect_check_health", handshake)
    monkeypatch.setattr(SentinelConnectionPool, "get_master_address", primary)
    client = create_redis_client(
        url=None if sentinel else "rediss://redis.test:6380",
        sentinel=(
            SentinelSettings(
                service_name="primary", addresses=(("sentinel.test", 26379),), tls=True
            )
            if sentinel
            else None
        ),
    )
    connection = client.connection_pool.make_connection()
    assert isinstance(connection, SSLConnection)
    if sentinel:
        assert isinstance(connection, SentinelManagedSSLConnection)
    heartbeat_task = asyncio.create_task(heartbeat())
    connect_task = asyncio.create_task(connection.connect())
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        before = ticks
        await asyncio.sleep(0.005)
        assert ticks > before
        assert not connect_task.done()
        released.set()
        await connect_task
        await connection.connect()
        assert loads == [loads[0]]
        assert loads[0] != loop_thread
        assert len(contexts) == 2
        assert contexts[0] is contexts[1] is connection.ssl_context.context
    finally:
        released.set()
        stopped.set()
        await asyncio.gather(connect_task, heartbeat_task, return_exceptions=True)
        await client.aclose()


async def test_tls_credential_warmup_obeys_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = threading.Event()
    finished = threading.Event()

    def slow_context(context: RedisSSLContext) -> ssl.SSLContext:
        if not released.wait(1):
            raise TimeoutError("test credential read did not finish")
        context.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        finished.set()
        return context.context

    monkeypatch.setattr(RedisSSLContext, "get", slow_context)
    client = create_redis_client(
        url="rediss://redis.test:6380?socket_connect_timeout=0.01"
    )
    connection = client.connection_pool.make_connection()
    assert isinstance(connection, SSLConnection)
    try:
        with pytest.raises(TimeoutError):
            await connection._connect()
    finally:
        released.set()
        assert await asyncio.to_thread(finished.wait, 0.5)
        await client.aclose()


async def test_tls_factory_preserves_url_and_sentinel_options_without_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_load(context: RedisSSLContext) -> ssl.SSLContext:
        raise AssertionError("The pure factory must not read TLS credentials")

    monkeypatch.setattr(RedisSSLContext, "get", unexpected_load)
    standalone = create_redis_client(
        url=(
            "rediss://reader:p%40ss@redis.test:6380/2?socket_timeout=7"
            "&ssl_ca_certs=%2Fca.pem&ssl_certfile=%2Fcert.pem"
            "&ssl_keyfile=%2Fkey.pem"
        ),
        socket_timeout=3,
    )
    sentinel = create_redis_client(
        url=None,
        socket_timeout=3,
        sentinel=SentinelSettings(
            service_name="primary",
            addresses=(("sentinel.test", 26379),),
            username="writer",
            password="primary-secret",
            sentinel_username="discovery",
            sentinel_password="discovery-secret",
            tls=True,
            ca_cert_path="/ca.pem",
            client_cert_path="/cert.pem",
            client_key_path="/key.pem",
        ),
    )
    try:
        direct = standalone.connection_pool.make_connection()
        managed = sentinel.connection_pool.make_connection()
        assert isinstance(direct, SSLConnection)
        assert isinstance(managed, SentinelManagedSSLConnection)
        for connection in (direct, managed):
            assert connection.ca_certs == "/ca.pem"
            assert connection.certfile == "/cert.pem"
            assert connection.keyfile == "/key.pem"
            assert connection.check_hostname
            assert connection.cert_reqs == ssl.CERT_REQUIRED
            assert connection.socket_connect_timeout == 5
            assert connection.encoder.decode_responses
            assert connection.protocol == 2
        assert (direct.host, direct.port, direct.db) == ("redis.test", 6380, 2)
        assert (direct.username, direct.password, direct.socket_timeout) == (
            "reader",
            "p@ss",
            7,
        )
        assert (managed.username, managed.password, managed.socket_timeout) == (
            "writer",
            "primary-secret",
            3,
        )
        pool = sentinel.connection_pool
        assert isinstance(pool, SentinelConnectionPool)
        assert pool.service_name == "primary"
        discovery = pool.sentinel_manager.sentinels[0].connection_pool.make_connection()
        assert isinstance(discovery, SSLConnection)
        assert discovery.__class__ is direct.__class__
        assert (discovery.host, discovery.port) == ("sentinel.test", 26379)
        assert (discovery.username, discovery.password, discovery.socket_timeout) == (
            "discovery",
            "discovery-secret",
            3,
        )
        assert discovery.ca_certs == "/ca.pem"
        assert discovery.certfile == "/cert.pem"
        assert discovery.keyfile == "/key.pem"
    finally:
        await asyncio.gather(standalone.aclose(), sentinel.aclose())


async def test_plain_redis_factory_keeps_ordinary_connection_class() -> None:
    client = create_redis_client(url="redis://redis.test:6379/4", socket_timeout=2)
    try:
        connection = client.connection_pool.make_connection()
        assert type(connection) is Connection
        assert (connection.host, connection.port, connection.db) == (
            "redis.test",
            6379,
            4,
        )
        assert connection.socket_timeout == 2
    finally:
        await client.aclose()
