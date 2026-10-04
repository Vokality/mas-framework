"""Audited enqueue respects independently configured storage and durability."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from mas_core.durability import (
    RedisDurability,
    RedisDurabilityError,
    RedisDurabilitySettings,
)
from mas_core.protocol import EnvelopeMessage
from mas_core.sessions import SessionLeaseStore
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.rate_limit import RateLimitModule
from mas_server.policy import PolicyPipeline
from mas_server.routing import MessageRouter
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from integration_tests.production_support import RedisNode

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True, slots=True)
class RedisBackends:
    """Two independent Redis databases on disposable AOF-backed storage."""

    audit: Redis
    routing: Redis


@pytest.fixture
async def backends(tmp_path: Path) -> AsyncIterator[RedisBackends]:
    node = RedisNode(tmp_path / "redis")
    await node.start()
    try:
        async with (
            Redis(
                host="127.0.0.1", port=node.port, db=0, decode_responses=True
            ) as audit,
            Redis(
                host="127.0.0.1", port=node.port, db=1, decode_responses=True
            ) as routing,
        ):
            yield RedisBackends(audit, routing)
    finally:
        await node.stop()


@asynccontextmanager
async def _policy(
    audit_redis: Redis,
    routing_redis: Redis,
    *,
    audit_durability: RedisDurability,
    routing_durability: RedisDurability,
) -> AsyncIterator[tuple[PolicyPipeline, AuditModule]]:
    authz = AuthorizationModule(audit_redis, enable_rbac=True)
    await authz.create_role("producer", permissions=["send:worker"])
    await authz.assign_role("sender", "producer")
    leases = SessionLeaseStore(audit_redis)
    lease = await leases.acquire("worker", "instance")
    audit = AuditModule(audit_redis, file_sink=None, durability=audit_durability)
    policy = PolicyPipeline(
        authz=authz,
        rate_limit=RateLimitModule(audit_redis, 100, 1000),
        audit=audit,
        router=MessageRouter(
            redis=routing_redis, dlq_enabled=True, durability=routing_durability
        ),
        dlp=None,
        circuit_breaker=None,
    )
    try:
        yield policy, audit
    finally:
        await audit.close()
        await leases.release(lease)


async def test_independent_router_backend_receives_the_envelope(
    backends: RedisBackends,
) -> None:
    durability = RedisDurability(RedisDurabilitySettings(wait_for_aof=True))
    async with _policy(
        backends.audit,
        backends.routing,
        audit_durability=durability,
        routing_durability=durability,
    ) as (policy, audit):
        message = EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="work", data={}
        )
        assert await policy.ingest_and_route(message) == message.message_id
        assert await backends.audit.xlen("agent.stream:worker") == 0
        assert await backends.routing.xlen("agent.stream:worker") == 1
        assert await backends.audit.xlen("audit:messages") == 1
        assert await backends.routing.xlen("audit:messages") == 0
        assert await audit.verify_integrity(message.message_id)


async def test_stricter_router_durability_is_required_before_acceptance(
    backends: RedisBackends,
) -> None:
    strict = RedisDurability(
        RedisDurabilitySettings(replica_count=1, wait_for_aof=True, timeout_ms=50)
    )
    async with _policy(
        backends.audit,
        backends.audit,
        audit_durability=RedisDurability(),
        routing_durability=strict,
    ) as (policy, audit):
        message = EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="work", data={}
        )
        with pytest.raises(
            RedisDurabilityError, match="configured_durability_unconfirmed"
        ):
            await policy.ingest_and_route(message)
        assert await backends.audit.xlen("agent.stream:worker") == 1
        assert await backends.audit.xlen("audit:messages") == 1
        assert await audit.verify_integrity(message.message_id)


async def test_shared_pool_and_policy_keep_audit_enqueue_atomic(
    backends: RedisBackends,
) -> None:
    durability = RedisDurability()
    await backends.audit.set("agent.stream:worker", "wrong-type")
    async with (
        Redis(connection_pool=backends.audit.connection_pool) as routing,
        _policy(
            backends.audit,
            routing,
            audit_durability=durability,
            routing_durability=durability,
        ) as (policy, _),
    ):
        message = EnvelopeMessage(
            sender_id="sender", target_id="worker", message_type="work", data={}
        )
        with pytest.raises(ResponseError):
            await policy.ingest_and_route(message)
        assert await backends.audit.xlen("audit:messages") == 0
        assert await backends.audit.xlen("audit:by_sender:sender") == 0
        assert await backends.audit.get("agent.stream:worker") == "wrong-type"
