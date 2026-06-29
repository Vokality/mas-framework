"""Shared pytest fixtures and configuration for all tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from mas_agent import TlsClientConfig
from mas_gateway.config import GatewaySettings, RedisSettings
from mas_server import AgentDefinition, MASServer
from mas_server.dev import (
    DEFAULT_DEV_AGENT_IDS,
    DevTlsBundle,
    dev_server_settings,
    generate_dev_tls,
)
from redis.asyncio import Redis

# Use anyio for async test support
pytestmark = pytest.mark.asyncio

_DEV_TEST_AGENT_IDS = DEFAULT_DEV_AGENT_IDS | frozenset({"attacker"})


@dataclass(slots=True)
class TestTlsPaths:
    """Test fixture wrapper preserving the historical ``test_tls`` surface."""

    bundle: DevTlsBundle

    @property
    def base_dir(self) -> Path:
        return self.bundle.base_dir

    @property
    def ca_pem(self) -> str:
        return self.bundle.ca_pem

    @property
    def ca_key(self) -> str:
        return self.bundle.ca_key

    @property
    def server_cert(self) -> str:
        return self.bundle.server_cert

    @property
    def server_key(self) -> str:
        return self.bundle.server_key

    def client(self, agent_id: str) -> TlsClientConfig:
        creds = self.bundle.client(agent_id)
        return TlsClientConfig(
            root_ca_path=creds.root_ca_path,
            client_cert_path=creds.client_cert_path,
            client_key_path=creds.client_key_path,
        )


@pytest.fixture
async def redis():
    """
    Redis connection fixture.

    Provides a Redis connection that is cleaned up after each test.
    Flushes the database before and after each test to ensure isolation.
    """
    r: Redis[str] = Redis.from_url("redis://localhost:6379", decode_responses=True)
    # Ensure a clean DB at the start of each test.
    await r.flushdb()
    yield r
    # Cleanup
    await r.flushdb()
    await r.aclose()


@pytest.fixture(autouse=True)
async def cleanup_agent_keys():
    """
    Auto-use fixture to clean up Redis agent keys and streams before each test.

    This ensures tests don't interfere with each other by cleaning up:
    - agent registration keys (agent:*)
    - agent state keys (agent.state:*)
    - agent delivery streams (agent.stream:*)
    - gateway streams (mas.gateway.*)

    This runs before the redis fixture cleanup, so it's safe for tests
    that use the redis fixture.
    """
    redis: Redis[str] = Redis.from_url("redis://localhost:6379", decode_responses=True)

    # Collect all keys/streams to delete
    keys_to_delete = []

    # Agent registration and heartbeat keys
    async for key in redis.scan_iter(match="agent:*"):
        keys_to_delete.append(key)

    # Agent state keys
    async for key in redis.scan_iter(match="agent.state:*"):
        keys_to_delete.append(key)

    # Agent delivery streams (including instance-specific streams)
    async for key in redis.scan_iter(match="agent.stream:*"):
        keys_to_delete.append(key)

    # Gateway streams (ingress, dlq, etc.)
    async for key in redis.scan_iter(match="mas.gateway.*"):
        keys_to_delete.append(key)

    # DLQ streams
    async for key in redis.scan_iter(match="dlq:*"):
        keys_to_delete.append(key)

    # Rate limit keys
    async for key in redis.scan_iter(match="rate_limit:*"):
        keys_to_delete.append(key)
    async for key in redis.scan_iter(match="ratelimit:*"):
        keys_to_delete.append(key)

    # ACL keys
    async for key in redis.scan_iter(match="acl:*"):
        keys_to_delete.append(key)

    # Audit keys
    async for key in redis.scan_iter(match="audit:*"):
        keys_to_delete.append(key)

    # Circuit breaker keys
    async for key in redis.scan_iter(match="circuit:*"):
        keys_to_delete.append(key)

    if keys_to_delete:
        await redis.delete(*keys_to_delete)

    await redis.aclose()
    yield


@pytest.fixture
def test_tls(tmp_path_factory) -> TestTlsPaths:
    base_dir = tmp_path_factory.mktemp("certs")
    bundle = generate_dev_tls(base_dir, agent_ids=_DEV_TEST_AGENT_IDS)
    return TestTlsPaths(bundle=bundle)


@pytest.fixture
async def mas_server_factory(
    test_tls: TestTlsPaths,
) -> AsyncGenerator[
    Callable[[dict[str, AgentDefinition] | None], Awaitable[MASServer]]
]:
    servers: list[MASServer] = []

    async def _start(agents: dict[str, AgentDefinition] | None = None) -> MASServer:
        agent_defs = agents or {}
        settings = dev_server_settings(
            agents=agent_defs,
            tls=test_tls.bundle,
            listen_addr="127.0.0.1:0",
        )
        gateway = GatewaySettings(redis=RedisSettings(url="redis://localhost:6379"))
        server = MASServer(settings=settings, gateway=gateway)
        await server.start()
        servers.append(server)
        return server

    yield _start

    for server in servers:
        await server.stop()
