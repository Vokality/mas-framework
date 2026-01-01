"""Shared pytest fixtures and configuration for all tests."""

import pytest
from redis.asyncio import Redis

from mas import MASService

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis():
    """
    Redis connection fixture.

    Provides a Redis connection that is cleaned up after each test.
    Flushes the database before and after each test to ensure isolation.
    """
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    # Ensure a clean DB at the start of each test.
    await r.flushdb()
    yield r
    # Cleanup
    await r.flushdb()
    await r.aclose()  # type: ignore[unresolved-attribute]


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
    redis = Redis.from_url("redis://localhost:6379", decode_responses=True)

    # Collect all keys/streams to delete
    keys_to_delete = []

    # Agent registration and heartbeat keys
    async for key in redis.scan_iter("agent:*"):
        keys_to_delete.append(key)

    # Agent state keys
    async for key in redis.scan_iter("agent.state:*"):
        keys_to_delete.append(key)

    # Agent delivery streams (including instance-specific streams)
    async for key in redis.scan_iter("agent.stream:*"):
        keys_to_delete.append(key)

    # Gateway streams (ingress, dlq, etc.)
    async for key in redis.scan_iter("mas.gateway.*"):
        keys_to_delete.append(key)

    # Rate limit keys
    async for key in redis.scan_iter("rate_limit:*"):
        keys_to_delete.append(key)

    # ACL keys
    async for key in redis.scan_iter("acl:*"):
        keys_to_delete.append(key)

    # Audit keys
    async for key in redis.scan_iter("audit:*"):
        keys_to_delete.append(key)

    if keys_to_delete:
        await redis.delete(*keys_to_delete)

    await redis.aclose()  # type: ignore[unresolved-attribute]
    yield


@pytest.fixture
async def mas_service():
    """
    MASService fixture for tests that need the service.

    Provides a MASService instance that is started and stopped automatically.
    """
    service = MASService(redis_url="redis://localhost:6379")
    await service.start()
    yield service
    await service.stop()
