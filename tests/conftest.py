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
    Flushes the database after each test to ensure isolation.
    """
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    yield r
    # Cleanup
    await r.flushdb()
    await r.aclose()


@pytest.fixture(autouse=True)
async def cleanup_agent_keys():
    """
    Auto-use fixture to clean up Redis agent keys before each test.

    This ensures tests don't interfere with each other by cleaning up
    agent registration keys and state keys. This runs before the redis
    fixture cleanup, so it's safe for tests that use the redis fixture.
    """
    redis = Redis.from_url("redis://localhost:6379", decode_responses=True)
    # Delete all test agent keys
    keys_to_delete = []
    async for key in redis.scan_iter("agent:*"):
        keys_to_delete.append(key)
    async for key in redis.scan_iter("agent.state:*"):
        keys_to_delete.append(key)

    if keys_to_delete:
        await redis.delete(*keys_to_delete)

    await redis.aclose()
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
