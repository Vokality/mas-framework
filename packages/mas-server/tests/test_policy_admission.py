"""Real policy admission preserves denial precedence when Redis checks are batched."""

from __future__ import annotations

from collections.abc import AsyncIterator

import grpc
import pytest
from mas_core.protocol import EnvelopeMessage
from mas_core.sessions import SessionLeaseStore
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.circuit_breaker import CircuitBreakerConfig, CircuitBreakerModule
from mas_gateway.rate_limit import RateLimitModule
from mas_server.errors import FailedPreconditionError, ResourceExhaustedError
from mas_server.policy import PolicyPipeline
from mas_server.routing import MessageRouter
from redis.asyncio import Redis
from redis.exceptions import ResponseError

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def policy(redis: Redis) -> AsyncIterator[PolicyPipeline]:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await SessionLeaseStore(redis).acquire("target", "live")
    await auth.set_permissions("sender", allowed_targets=["target"])
    audit = AuditModule(redis, file_sink=None)
    try:
        yield PolicyPipeline(
            authz=auth,
            rate_limit=RateLimitModule(
                redis, default_per_minute=5, default_per_hour=50
            ),
            audit=audit,
            router=MessageRouter(redis=redis, dlq_enabled=True),
            dlp=None,
            circuit_breaker=CircuitBreakerModule(redis, CircuitBreakerConfig()),
        )
    finally:
        await audit.close()


@pytest.fixture
def message() -> EnvelopeMessage:
    return EnvelopeMessage(
        sender_id="sender", target_id="target", message_type="work", data={}
    )


async def test_rate_denial_precedes_circuit_read_failure_and_is_audited(
    redis: Redis, policy: PolicyPipeline, message: EnvelopeMessage
) -> None:
    await policy._rate_limit.set_limits("sender", per_minute=0)
    await redis.set("circuit:target", "wrong-type")
    with pytest.raises(ResourceExhaustedError) as error:
        await policy.ingest_and_route(message)
    assert error.value.status == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert error.value.message == "rate_limited"
    records = await policy._audit.query_recent()
    assert len(records) == 1
    assert records[0].decision == "RATE_LIMITED"
    assert await policy._rate_limit.get_current_usage("sender") == {
        "per_minute": 0,
        "per_hour": 0,
    }
    assert await redis.xlen("agent.stream:target") == 0


async def test_rate_storage_error_precedes_circuit_storage_error(
    redis: Redis, policy: PolicyPipeline, message: EnvelopeMessage
) -> None:
    await redis.hset("ratelimit:sender:limits", mapping={"per_minute": "invalid"})
    await redis.set("circuit:target", "wrong-type")
    with pytest.raises(ResponseError, match="Rate limits must be nonnegative integers"):
        await policy.ingest_and_route(message)
    assert await policy._rate_limit.get_current_usage("sender") == {
        "per_minute": 0,
        "per_hour": 0,
    }
    assert await redis.xlen("agent.stream:target") == 0


async def test_circuit_storage_error_after_rate_admission_does_not_route(
    redis: Redis, policy: PolicyPipeline, message: EnvelopeMessage
) -> None:
    await redis.set("circuit:target", "wrong-type")
    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await policy.ingest_and_route(message)
    assert await policy._rate_limit.get_current_usage("sender") == {
        "per_minute": 1,
        "per_hour": 1,
    }
    assert await redis.xlen("agent.stream:target") == 0
    assert await policy._audit.query_recent() == []


async def test_open_circuit_is_audited_after_rate_admission(
    redis: Redis, policy: PolicyPipeline, message: EnvelopeMessage
) -> None:
    assert policy._circuit_breaker is not None
    for _ in range(5):
        await policy._circuit_breaker.record_failure("target")
    with pytest.raises(FailedPreconditionError) as error:
        await policy.ingest_and_route(message)
    assert error.value.status == grpc.StatusCode.FAILED_PRECONDITION
    assert error.value.message == "circuit_open"
    records = await policy._audit.query_recent()
    assert len(records) == 1
    assert records[0].decision == "CIRCUIT_OPEN"
    assert await policy._rate_limit.get_current_usage("sender") == {
        "per_minute": 1,
        "per_hour": 1,
    }
    assert await redis.xlen("agent.stream:target") == 0


async def test_allowed_pipelined_admission_routes_and_audits_once(
    redis: Redis, policy: PolicyPipeline, message: EnvelopeMessage
) -> None:
    assert await policy.ingest_and_route(message) == message.message_id
    assert await redis.xlen("agent.stream:target") == 1
    records = await policy._audit.query_recent()
    assert len(records) == 1
    assert records[0].decision == "ALLOWED"
    assert await policy._audit.verify_integrity(message.message_id)
