"""Queued admission checks retain ordering, validation, and circuit concurrency."""

from __future__ import annotations

import asyncio

import pytest
from mas_gateway.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerModule,
    CircuitState,
    CircuitStatus,
)
from mas_gateway.rate_limit import RateLimitModule
from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

pytestmark = pytest.mark.asyncio
_RESULTS = TypeAdapter(tuple[object, object])


async def test_queued_admission_defers_writes_and_resolves_closed_without_reread(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    rate = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    circuit = CircuitBreakerModule(redis, CircuitBreakerConfig())

    async def unexpected_transition(target_id: str) -> CircuitStatus:
        raise AssertionError(f"Unchanged circuit unexpectedly reread: {target_id}")

    monkeypatch.setattr(circuit, "check_circuit", unexpected_transition)
    async with redis.pipeline(transaction=False) as pipeline:
        rate.queue_check(pipeline, "sender", "message")
        circuit.queue_check(pipeline, "target")
        assert await rate.get_current_usage("sender") == {
            "per_minute": 0,
            "per_hour": 0,
        }
        raw_rate, raw_circuit = _RESULTS.validate_python(
            await pipeline.execute(raise_on_error=False)
        )
    assert rate.parse_result(raw_rate).allowed
    status = await circuit.resolve_check(raw_circuit, "target")
    assert status.state == CircuitState.CLOSED
    assert status.allowed
    assert await rate.get_current_usage("sender") == {"per_minute": 1, "per_hour": 1}
    assert await redis.hgetall("circuit:target") == {}


async def test_rate_denial_can_be_resolved_before_later_circuit_storage_error(
    redis: Redis,
) -> None:
    rate = RateLimitModule(redis, default_per_minute=0, default_per_hour=50)
    circuit = CircuitBreakerModule(redis, CircuitBreakerConfig())
    await redis.set("circuit:target", "wrong-type")
    async with redis.pipeline(transaction=False) as pipeline:
        rate.queue_check(pipeline, "sender", "message")
        circuit.queue_check(pipeline, "target")
        raw_rate, raw_circuit = _RESULTS.validate_python(
            await pipeline.execute(raise_on_error=False)
        )
    assert not rate.parse_result(raw_rate).allowed
    assert isinstance(raw_circuit, RedisError)
    with pytest.raises(ResponseError):
        await circuit.resolve_check(raw_circuit, "target")
    assert await rate.get_current_usage("sender") == {"per_minute": 0, "per_hour": 0}


async def test_expired_queued_snapshot_cannot_overwrite_newer_open_transition(
    redis: Redis,
) -> None:
    now = 100.0
    circuit = CircuitBreakerModule(
        redis,
        CircuitBreakerConfig(failure_threshold=1, timeout_seconds=10),
        clock=lambda: now,
    )
    await circuit.record_failure("target")
    async with redis.pipeline(transaction=False) as pipeline:
        circuit.queue_check(pipeline, "target")
        (raw,) = TypeAdapter(tuple[object]).validate_python(await pipeline.execute())
    now = 110.0
    await circuit.reset_circuit("target")
    await circuit.record_failure("target")
    now = 111.0
    status = await circuit.resolve_check(raw, "target")
    assert status.state == CircuitState.OPEN
    assert not status.allowed
    assert status.opened_at == 110.0
    assert (await circuit.check_circuit("target")) == status


async def test_concurrent_queued_timeout_resolution_preserves_half_open_counts(
    redis: Redis,
) -> None:
    now = 100.0
    circuit = CircuitBreakerModule(
        redis,
        CircuitBreakerConfig(failure_threshold=1, timeout_seconds=10),
        clock=lambda: now,
    )
    await circuit.record_failure("target")
    async with redis.pipeline(transaction=False) as pipeline:
        circuit.queue_check(pipeline, "target")
        (raw,) = TypeAdapter(tuple[object]).validate_python(await pipeline.execute())
    now = 111.0
    first, second = await asyncio.gather(
        circuit.resolve_check(raw, "target"), circuit.resolve_check(raw, "target")
    )
    assert first.state == second.state == CircuitState.HALF_OPEN
    assert first.success_count == second.success_count == 0
    assert (await circuit.record_success("target")).success_count == 1


@pytest.mark.parametrize("module", ["rate", "circuit"])
async def test_queue_requires_an_unwatched_pipeline(redis: Redis, module: str) -> None:
    rate = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    circuit = CircuitBreakerModule(redis, CircuitBreakerConfig())
    async with redis.pipeline(transaction=False) as pipeline:
        await pipeline.watch("guard")
        with pytest.raises(ValueError, match="unwatched pipeline"):
            if module == "rate":
                rate.queue_check(pipeline, "sender", "message")
            else:
                circuit.queue_check(pipeline, "target")


async def test_queued_malformed_quota_preserves_storage_failure(redis: Redis) -> None:
    rate = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    await redis.hset("ratelimit:sender:limits", mapping={"per_minute": "invalid"})
    async with redis.pipeline(transaction=False) as pipeline:
        rate.queue_check(pipeline, "sender", "message")
        (raw,) = TypeAdapter(tuple[object]).validate_python(
            await pipeline.execute(raise_on_error=False)
        )
    assert isinstance(raw, ResponseError)
    with pytest.raises(ResponseError) as error:
        rate.parse_result(raw)
    assert error.value is raw
    assert await rate.get_current_usage("sender") == {"per_minute": 0, "per_hour": 0}


async def test_parsers_validate_unknown_results_before_domain_use(redis: Redis) -> None:
    rate = RateLimitModule(redis, default_per_minute=5, default_per_hour=50)
    circuit = CircuitBreakerModule(redis, CircuitBreakerConfig())
    with pytest.raises(ValidationError):
        rate.parse_result([1, 5, 4, "nan", "minute"])
    with pytest.raises(ValidationError):
        await circuit.resolve_check({"state": "invalid"}, "target")
    assert await redis.hgetall("circuit:target") == {}
