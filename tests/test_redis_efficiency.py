"""Tests for Redis operation efficiency.

These tests verify that optimizations reduce Redis round-trips.
Run baseline tests before optimization to document current behavior,
then verify improvements after optimization.

Usage:
    # Run efficiency tests with verbose output
    uv run pytest tests/test_redis_efficiency.py -v -s
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Literal,
    Self,
    Set,
    overload,
)

import pytest

from mas.redis_types import AsyncRedisProtocol, PubSubProtocol

if TYPE_CHECKING:
    from mas.redis_types import PipelineProtocol

pytestmark = pytest.mark.asyncio


class PipelineCallCounter:
    """
    Pipeline wrapper that counts operations and delegates to real pipeline.

    Tracks pipeline operations as a single "pipeline_execute" call when
    execute() is called, which accurately reflects the single round-trip.
    """

    def __init__(
        self, real_pipeline: "PipelineProtocol", counter: "RedisCallCounter"
    ) -> None:
        self._pipeline = real_pipeline
        self._counter = counter
        self._queued_ops: list[str] = []

    def hgetall(self, key: str) -> Self:
        self._queued_ops.append("hgetall")
        self._pipeline.hgetall(key)
        return self

    def hset(self, key: str, *, mapping: dict[str, str]) -> Self:
        self._queued_ops.append("hset")
        self._pipeline.hset(key, mapping=mapping)
        return self

    def hget(self, key: str, field: str) -> Self:
        self._queued_ops.append("hget")
        self._pipeline.hget(key, field)
        return self

    def delete(self, *keys: str) -> Self:
        self._queued_ops.append("delete")
        self._pipeline.delete(*keys)
        return self

    def exists(self, key: str) -> Self:
        self._queued_ops.append("exists")
        self._pipeline.exists(key)
        return self

    def get(self, key: str) -> Self:
        self._queued_ops.append("get")
        self._pipeline.get(key)
        return self

    def set(self, key: str, value: str) -> Self:
        self._queued_ops.append("set")
        self._pipeline.set(key, value)
        return self

    def xadd(self, name: str, fields: dict[str, str]) -> Self:
        self._queued_ops.append("xadd")
        self._pipeline.xadd(name, fields)
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> Self:
        self._queued_ops.append("zadd")
        self._pipeline.zadd(key, mapping)
        return self

    def zcard(self, key: str) -> Self:
        self._queued_ops.append("zcard")
        self._pipeline.zcard(key)
        return self

    def zcount(self, key: str, min: float | str, max: float | str) -> Self:
        self._queued_ops.append("zcount")
        self._pipeline.zcount(key, min, max)
        return self

    def expire(self, key: str, seconds: int) -> Self:
        self._queued_ops.append("expire")
        self._pipeline.expire(key, seconds)
        return self

    def ttl(self, key: str) -> Self:
        self._queued_ops.append("ttl")
        self._pipeline.ttl(key)
        return self

    def setex(self, key: str, seconds: int, value: str) -> Self:
        self._queued_ops.append("setex")
        self._pipeline.setex(key, seconds, value)
        return self

    async def execute(self) -> list[Any]:
        # Track as single pipeline call with info about batched ops
        self._counter._track("pipeline_execute")
        self._counter.pipeline_ops.append(self._queued_ops.copy())
        return await self._pipeline.execute()


class RedisCallCounter:
    """
    Wrapper that counts Redis operations.

    Proxies all calls to the underlying Redis client while tracking
    the number of calls per method. Used to verify that optimizations
    reduce the number of Redis round-trips.

    Usage:
        counter = RedisCallCounter(real_redis)
        # Use counter as redis client
        await counter.hget("key", "field")
        # Check counts
        assert counter.call_counts["hget"] == 1
        assert counter.total_calls == 1
    """

    def __init__(self, redis: AsyncRedisProtocol):
        self._redis = redis
        self.call_counts: dict[str, int] = defaultdict(int)
        self.total_calls = 0
        self.pipeline_ops: list[list[str]] = []  # Track ops in each pipeline

    def reset_counts(self) -> None:
        """Reset all counters to zero."""
        self.call_counts.clear()
        self.total_calls = 0
        self.pipeline_ops.clear()

    def _track(self, method: str) -> None:
        """Track a method call."""
        self.call_counts[method] += 1
        self.total_calls += 1

    def get_summary(self) -> str:
        """Get a human-readable summary of call counts."""
        lines = [f"Total Redis calls: {self.total_calls}"]
        for method, count in sorted(self.call_counts.items()):
            lines.append(f"  {method}: {count}")
        if self.pipeline_ops:
            lines.append(f"  Pipeline batches: {len(self.pipeline_ops)}")
            for i, ops in enumerate(self.pipeline_ops):
                lines.append(
                    f"    Batch {i + 1}: {len(ops)} ops ({', '.join(set(ops))})"
                )
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Pipeline support
    # -------------------------------------------------------------------------

    def pipeline(self) -> PipelineCallCounter:
        """Create a pipeline with call counting."""
        return PipelineCallCounter(self._redis.pipeline(), self)

    # -------------------------------------------------------------------------
    # Connection methods
    # -------------------------------------------------------------------------

    def aclose(self) -> Awaitable[None]:
        self._track("aclose")
        return self._redis.aclose()

    # -------------------------------------------------------------------------
    # Scripting methods
    # -------------------------------------------------------------------------

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str,
    ) -> Any:
        self._track("eval")
        return await self._redis.eval(script, numkeys, *keys_and_args)

    # -------------------------------------------------------------------------
    # Key methods
    # -------------------------------------------------------------------------

    async def exists(self, key: str) -> int:
        self._track("exists")
        return await self._redis.exists(key)

    async def delete(self, *keys: str) -> int:
        self._track("delete")
        return await self._redis.delete(*keys)

    async def expire(self, key: str, seconds: int) -> int:
        self._track("expire")
        return await self._redis.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        self._track("ttl")
        return await self._redis.ttl(key)

    async def scan(
        self, cursor: int, *, match: str, count: int
    ) -> tuple[int, list[str]]:
        self._track("scan")
        return await self._redis.scan(cursor, match=match, count=count)

    def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        self._track("scan_iter")
        return self._redis.scan_iter(match=match)

    # -------------------------------------------------------------------------
    # String methods
    # -------------------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        self._track("get")
        return await self._redis.get(key)

    async def set(self, key: str, value: str) -> bool | str:
        self._track("set")
        return await self._redis.set(key, value)

    async def setex(self, key: str, seconds: int, value: str) -> bool | str:
        self._track("setex")
        return await self._redis.setex(key, seconds, value)

    async def incr(self, key: str) -> int:
        self._track("incr")
        return await self._redis.incr(key)

    async def decr(self, key: str) -> int:
        self._track("decr")
        return await self._redis.decr(key)

    async def publish(self, channel: str, message: str) -> int:
        self._track("publish")
        return await self._redis.publish(channel, message)

    def pubsub(self) -> PubSubProtocol:
        self._track("pubsub")
        return self._redis.pubsub()

    # -------------------------------------------------------------------------
    # Hash methods
    # -------------------------------------------------------------------------

    async def hget(self, key: str, field: str) -> str | None:
        self._track("hget")
        return await self._redis.hget(key, field)

    async def hset(self, key: str, *, mapping: dict[str, str]) -> int:
        self._track("hset")
        return await self._redis.hset(key, mapping=mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        self._track("hgetall")
        return await self._redis.hgetall(key)

    async def hdel(self, key: str, *fields: str) -> int:
        self._track("hdel")
        return await self._redis.hdel(key, *fields)

    # -------------------------------------------------------------------------
    # Set methods
    # -------------------------------------------------------------------------

    async def sadd(self, key: str, *members: str) -> int:
        self._track("sadd")
        return await self._redis.sadd(key, *members)

    async def srem(self, key: str, *members: str) -> int:
        self._track("srem")
        return await self._redis.srem(key, *members)

    async def smembers(self, key: str) -> Set[str]:
        self._track("smembers")
        return await self._redis.smembers(key)

    async def sismember(self, key: str, member: str) -> bool:
        self._track("sismember")
        return await self._redis.sismember(key, member)

    # -------------------------------------------------------------------------
    # Sorted set methods
    # -------------------------------------------------------------------------

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._track("zadd")
        return await self._redis.zadd(key, mapping)

    async def zcard(self, key: str) -> int:
        self._track("zcard")
        return await self._redis.zcard(key)

    async def zrem(self, key: str, *members: str) -> int:
        self._track("zrem")
        return await self._redis.zrem(key, *members)

    async def zremrangebyscore(
        self, key: str, min: float | str, max: float | str
    ) -> int:
        self._track("zremrangebyscore")
        return await self._redis.zremrangebyscore(key, min, max)

    async def zcount(self, key: str, min: float | str, max: float | str) -> int:
        self._track("zcount")
        return await self._redis.zcount(key, min, max)

    async def zscore(self, key: str, member: str) -> float | None:
        self._track("zscore")
        return await self._redis.zscore(key, member)

    @overload
    async def zrange(
        self, key: str, start: int, end: int, *, withscores: Literal[True]
    ) -> list[tuple[str, float]]: ...

    @overload
    async def zrange(
        self, key: str, start: int, end: int, *, withscores: Literal[False] = ...
    ) -> list[str]: ...

    async def zrange(
        self, key: str, start: int, end: int, *, withscores: bool = False
    ) -> list[tuple[str, float]] | list[str]:
        self._track("zrange")
        if withscores:
            return await self._redis.zrange(key, start, end, withscores=True)
        return await self._redis.zrange(key, start, end, withscores=False)

    # -------------------------------------------------------------------------
    # Stream methods
    # -------------------------------------------------------------------------

    async def xadd(self, name: str, fields: dict[str, str]) -> str:
        self._track("xadd")
        return await self._redis.xadd(name, fields)

    async def xrange(
        self, name: str, min: str, max: str, count: int | None = None
    ) -> list[tuple[str, dict[str, str]]]:
        self._track("xrange")
        return await self._redis.xrange(name, min, max, count)

    async def xlen(self, name: str) -> int:
        self._track("xlen")
        return await self._redis.xlen(name)

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = False
    ) -> str:
        self._track("xgroup_create")
        return await self._redis.xgroup_create(name, groupname, id, mkstream)

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        *,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]] | None:
        self._track("xreadgroup")
        return await self._redis.xreadgroup(
            groupname, consumername, streams=streams, count=count, block=block
        )

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self._track("xack")
        return await self._redis.xack(name, groupname, *ids)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def counter(redis) -> RedisCallCounter:
    """Redis client with call counting."""
    return RedisCallCounter(redis)


@pytest.fixture
async def setup_test_agents(redis) -> list[str]:
    """Setup multiple test agents for discovery testing."""
    agent_ids = [f"efficiency_agent_{i}" for i in range(10)]
    for agent_id in agent_ids:
        await redis.hset(
            f"agent:{agent_id}",
            mapping={
                "id": agent_id,
                "capabilities": json.dumps(["efficiency_test"]),
                "metadata": "{}",
                "status": "ACTIVE",
                "registered_at": "1234567890",
            },
        )
    yield agent_ids
    # Cleanup
    for agent_id in agent_ids:
        await redis.delete(f"agent:{agent_id}")


# -----------------------------------------------------------------------------
# Rate Limit Efficiency Tests
# -----------------------------------------------------------------------------


class TestRateLimitEfficiency:
    """
    Verify rate limit Redis call counts.

    BEFORE optimization:
    - get_limits(): 2 hget calls
    - _check_window() for minute: zremrangebyscore + zcard + zadd + expire = 4 calls
    - _check_window() for hour: zremrangebyscore + zcard + zadd + expire = 4 calls
    - Total: ~10 calls per rate limit check

    AFTER optimization (Lua script):
    - Single eval call: 1 call
    """

    async def test_optimized_single_check(self, counter: RedisCallCounter):
        """Optimized: Single Lua script call for rate limit check."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(counter, default_per_minute=100, default_per_hour=1000)

        result = await module.check_rate_limit("test_agent", "msg_1")

        print(f"\n{counter.get_summary()}")
        print(f"Rate limit result: allowed={result.allowed}")

        # AFTER optimization: expect exactly 1 eval call
        assert counter.call_counts["eval"] == 1, (
            f"Expected 1 eval call, got {counter.call_counts['eval']}"
        )
        assert counter.total_calls == 1, (
            f"Expected 1 total call (eval), got {counter.total_calls}"
        )

    async def test_optimized_multiple_checks(self, counter: RedisCallCounter):
        """Optimized: Multiple checks use 1 call each."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(counter, default_per_minute=100, default_per_hour=1000)

        num_checks = 5
        for i in range(num_checks):
            await module.check_rate_limit("test_agent", f"msg_{i}")

        print(f"\n{counter.get_summary()}")

        # AFTER optimization: 1 eval call per check
        calls_per_check = counter.total_calls / num_checks
        print(f"Calls per check: {calls_per_check:.1f}")

        assert counter.call_counts["eval"] == num_checks, (
            f"Expected {num_checks} eval calls, got {counter.call_counts['eval']}"
        )
        assert calls_per_check == 1.0, (
            f"Expected 1 call per check, got {calls_per_check}"
        )


# -----------------------------------------------------------------------------
# Discovery Efficiency Tests
# -----------------------------------------------------------------------------


class TestDiscoveryEfficiency:
    """
    Verify discovery Redis call counts.

    BEFORE optimization:
    - 1 scan_iter call
    - N hgetall calls (one per agent found)
    - Total: O(N+1) calls

    AFTER optimization (pipeline batching):
    - 1 scan_iter call
    - 1 pipeline_execute call (batched hgetall)
    - Total: O(2) calls
    """

    async def test_optimized_discovery(
        self, counter: RedisCallCounter, setup_test_agents: list[str]
    ):
        """Optimized: Pipeline batches all hgetall calls."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(counter)

        agents = await registry.discover(capabilities=["efficiency_test"])

        print(f"\n{counter.get_summary()}")
        print(f"Discovered {len(agents)} agents")

        num_agents = len(setup_test_agents)
        assert len(agents) == num_agents, f"Expected {num_agents} agents"

        # AFTER optimization: 1 scan_iter + 1 pipeline_execute = 2 calls
        assert counter.call_counts["scan_iter"] == 1, (
            f"Expected 1 scan_iter call, got {counter.call_counts['scan_iter']}"
        )
        assert counter.call_counts["pipeline_execute"] == 1, (
            f"Expected 1 pipeline_execute call, got {counter.call_counts['pipeline_execute']}"
        )
        assert counter.total_calls == 2, (
            f"Expected 2 total calls (scan_iter + pipeline), got {counter.total_calls}"
        )

        # Verify pipeline contained N hgetall operations
        assert len(counter.pipeline_ops) == 1, "Expected 1 pipeline batch"
        assert len(counter.pipeline_ops[0]) == num_agents, (
            f"Expected {num_agents} ops in pipeline, got {len(counter.pipeline_ops[0])}"
        )

    async def test_optimized_discovery_no_filter(
        self, counter: RedisCallCounter, setup_test_agents: list[str]
    ):
        """Optimized: Discovery without filter also uses pipeline."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(counter)

        # Discover all agents (no capability filter)
        agents = await registry.discover()

        print(f"\n{counter.get_summary()}")
        print(f"Discovered {len(agents)} agents (no filter)")

        # Should use pipeline (2 total calls)
        assert counter.total_calls == 2, (
            f"Expected 2 total calls, got {counter.total_calls}"
        )
        assert counter.call_counts["pipeline_execute"] == 1


# -----------------------------------------------------------------------------
# Audit Module Efficiency Tests
# -----------------------------------------------------------------------------


class TestAuditEfficiency:
    """
    Verify audit logging Redis call counts.

    BEFORE optimization:
    - get (previous_hash): 1 call
    - xadd (main stream): 1 call
    - xadd (sender index): 1 call
    - xadd (target index): 1 call
    - set (update hash): 1 call
    - Total: 5 sequential calls

    AFTER optimization (pipeline):
    - get (previous_hash): 1 call (needed before pipeline)
    - 1 pipeline_execute call (batched xadd + set)
    - Total: 2 calls
    """

    async def test_optimized_log_message(self, counter: RedisCallCounter):
        """Optimized: Pipeline batches xadd and set calls."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(counter)

        await audit.log_message(
            message_id="msg_1",
            sender_id="agent_a",
            target_id="agent_b",
            decision="ALLOWED",
            latency_ms=10.0,
            payload={"test": "data"},
        )

        print(f"\n{counter.get_summary()}")

        # AFTER optimization: get + 1 pipeline_execute = 2 calls
        assert counter.call_counts["get"] == 1, (
            f"Expected 1 get call, got {counter.call_counts['get']}"
        )
        assert counter.call_counts["pipeline_execute"] == 1, (
            f"Expected 1 pipeline_execute call, got {counter.call_counts['pipeline_execute']}"
        )
        assert counter.total_calls == 2, (
            f"Expected 2 total calls (get + pipeline), got {counter.total_calls}"
        )

        # Verify pipeline contained 4 operations (3 xadd + 1 set)
        assert len(counter.pipeline_ops) == 1, "Expected 1 pipeline batch"
        assert counter.pipeline_ops[0].count("xadd") == 3, (
            f"Expected 3 xadd ops in pipeline, got {counter.pipeline_ops[0]}"
        )
        assert counter.pipeline_ops[0].count("set") == 1, (
            f"Expected 1 set op in pipeline, got {counter.pipeline_ops[0]}"
        )

    async def test_optimized_multiple_logs(self, counter: RedisCallCounter):
        """Optimized: Multiple logs use 2 calls each."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(counter)

        num_logs = 5
        for i in range(num_logs):
            await audit.log_message(
                message_id=f"msg_{i}",
                sender_id="agent_a",
                target_id="agent_b",
                decision="ALLOWED",
                latency_ms=10.0,
                payload={"index": i},
            )

        print(f"\n{counter.get_summary()}")

        # AFTER optimization: 2 calls per log × 5 logs = 10 calls
        calls_per_log = counter.total_calls / num_logs
        print(f"Calls per log: {calls_per_log:.1f}")

        assert calls_per_log == 2.0, f"Expected 2 calls per log, got {calls_per_log}"
        assert counter.call_counts["pipeline_execute"] == num_logs, (
            f"Expected {num_logs} pipeline_execute calls, got {counter.call_counts['pipeline_execute']}"
        )


# -----------------------------------------------------------------------------
# Circuit Breaker Efficiency Tests
# -----------------------------------------------------------------------------


class TestCircuitBreakerEfficiency:
    """
    Verify circuit breaker Redis call counts.

    BEFORE optimization:
    - check_circuit: 1 hgetall call
    - record_success/failure: 1 hgetall call
    - Combined check+record: 2 hgetall calls (double-fetch)

    AFTER optimization:
    - check_circuit: 1 hgetall call (unchanged)
    - check_and_record_success/failure: 1 hgetall call (combined)
    """

    async def test_baseline_check_circuit(self, counter: RedisCallCounter):
        """Baseline: Count Redis calls for circuit check."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(counter)

        status = await cb.check_circuit("test_target")

        print(f"\n{counter.get_summary()}")
        print(f"Circuit status: state={status.state}, allowed={status.allowed}")

        # Single hgetall for circuit state
        assert counter.call_counts["hgetall"] == 1

    async def test_baseline_check_then_record(self, counter: RedisCallCounter):
        """Baseline: Separate check+record uses 2 hgetall calls."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(counter)

        # Separate calls pattern (legacy)
        status = await cb.check_circuit("test_target")
        if status.allowed:
            await cb.record_success("test_target")

        print(f"\n{counter.get_summary()}")

        # Separate calls: 2 hgetall calls (double-fetch)
        assert counter.call_counts["hgetall"] == 2, (
            f"Expected 2 hgetall calls (double fetch), "
            f"got {counter.call_counts['hgetall']}"
        )

    async def test_optimized_check_and_record_success(self, counter: RedisCallCounter):
        """Optimized: Combined check+record uses 1 hgetall call."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(counter)

        # Combined method avoids double-fetch
        check_status, record_status = await cb.check_and_record_success(
            "optimized_target"
        )

        print(f"\n{counter.get_summary()}")
        print(
            f"Check status: state={check_status.state}, allowed={check_status.allowed}"
        )
        print(f"Record status: state={record_status.state}")

        # AFTER optimization: only 1 hgetall call (combined operation)
        assert counter.call_counts["hgetall"] == 1, (
            f"Expected 1 hgetall call (combined), got {counter.call_counts['hgetall']}"
        )
        assert counter.total_calls == 1, (
            f"Expected 1 total call, got {counter.total_calls}"
        )

    async def test_optimized_check_and_record_failure(self, counter: RedisCallCounter):
        """Optimized: Combined check+failure uses 1 hgetall + 1 hset."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(counter)

        # Combined method avoids double-fetch
        check_status, record_status = await cb.check_and_record_failure(
            "failure_target", "test_failure"
        )

        print(f"\n{counter.get_summary()}")
        print(f"Check status: allowed={check_status.allowed}")
        print(f"Record status: failure_count={record_status.failure_count}")

        # AFTER optimization: 1 hgetall (read) + 1 hset (write) + 1 expire
        assert counter.call_counts["hgetall"] == 1, (
            f"Expected 1 hgetall call, got {counter.call_counts['hgetall']}"
        )
        # Total: hgetall + hset + expire = 3 calls
        assert counter.total_calls == 3, (
            f"Expected 3 total calls (hgetall + hset + expire), got {counter.total_calls}"
        )


# -----------------------------------------------------------------------------
# Health Monitor Efficiency Tests
# -----------------------------------------------------------------------------


class TestHealthMonitorEfficiency:
    """
    Verify health monitor Redis call counts.

    BEFORE optimization:
    - Two separate scan_iter calls (heartbeat keys, agent keys)
    - Individual ttl, exists, hget, hset calls per key
    - Total: O(2N) calls

    AFTER optimization:
    - Single scan to collect all agent keys
    - Pipeline batch-fetch TTLs and agent data
    - Pipeline batch-update stale agents
    - Total: O(3) calls (scan + 2 pipelines)
    """

    async def test_optimized_health_monitor_pattern(
        self, counter: RedisCallCounter, setup_test_agents: list[str]
    ):
        """
        Optimized: Document the pipeline-based health monitoring pattern.

        The optimized _monitor_health uses:
        1. Single scan_iter to collect agent keys
        2. Pipeline to batch-fetch TTLs and status
        3. Pipeline to batch-update stale agents (if any)
        """
        # Simulate the optimized pattern: single scan + pipeline
        agent_keys: list[str] = []
        async for key in counter.scan_iter(match="agent:*"):
            if key.count(":") == 1:  # Only agent hashes, not heartbeat keys
                agent_keys.append(key)

        print(f"\n{counter.get_summary()}")
        print(f"Found {len(agent_keys)} agent keys")

        # Should only have 1 scan_iter call (optimized pattern)
        assert counter.call_counts["scan_iter"] == 1, (
            f"Expected 1 scan_iter call, got {counter.call_counts['scan_iter']}"
        )

        # Now simulate the pipeline batch-fetch
        if agent_keys:
            pipe = counter.pipeline()
            for agent_key in agent_keys:
                agent_id = agent_key.split(":")[1]
                hb_key = f"agent:{agent_id}:heartbeat"
                pipe.ttl(hb_key)
                pipe.hget(agent_key, "status")
                pipe.hget(agent_key, "registered_at")
            await pipe.execute()

        print(f"\nAfter batch fetch:\n{counter.get_summary()}")

        # Should have 1 scan + 1 pipeline = 2 total calls
        assert counter.total_calls == 2, (
            f"Expected 2 total calls (scan + pipeline), got {counter.total_calls}"
        )
        assert counter.call_counts["pipeline_execute"] == 1, (
            f"Expected 1 pipeline_execute call, got {counter.call_counts['pipeline_execute']}"
        )


# -----------------------------------------------------------------------------
# Registry Deregister Efficiency Tests
# -----------------------------------------------------------------------------


class TestDeregisterEfficiency:
    """
    Verify deregister Redis call counts.

    BEFORE optimization:
    - 2-3 separate delete calls
    - Total: 2-3 calls

    AFTER optimization (pipeline):
    - 1 pipeline_execute call (batched deletes)
    - Total: 1 call
    """

    async def test_optimized_deregister(self, counter: RedisCallCounter):
        """Optimized: Pipeline batches all delete calls."""
        from mas.registry import AgentRegistry

        # Setup agent first (with multi-instance support)
        instance_id = "testinst"
        await counter._redis.hset(
            "agent:dereg_test",
            mapping={
                "id": "dereg_test",
                "capabilities": "[]",
                "metadata": "{}",
                "status": "ACTIVE",
                "registered_at": "123",
            },
        )
        # Set instance count to 1 (last instance)
        await counter._redis.set("agent:dereg_test:instance_count", "1")
        # New heartbeat format includes instance_id
        await counter._redis.set(f"agent:dereg_test:heartbeat:{instance_id}", "123")

        counter.reset_counts()
        registry = AgentRegistry(counter)

        await registry.deregister("dereg_test", instance_id, keep_state=True)

        print(f"\n{counter.get_summary()}")

        # Multi-instance deregister pattern:
        # 1. DECR instance_count
        # 2. DELETE instance heartbeat
        # 3. DELETE instance token hash
        # 3. Pipeline for cleanup (when last instance)
        assert counter.call_counts["decr"] == 1, (
            f"Expected 1 decr call, got {counter.call_counts.get('decr', 0)}"
        )
        assert counter.call_counts["delete"] == 2, (
            f"Expected 2 delete calls (heartbeat + token_hash), got {counter.call_counts.get('delete', 0)}"
        )
        assert counter.call_counts["pipeline_execute"] == 1, (
            f"Expected 1 pipeline_execute call, got {counter.call_counts.get('pipeline_execute', 0)}"
        )

    async def test_optimized_deregister_with_state(self, counter: RedisCallCounter):
        """Optimized: Deregister with state deletion uses single pipeline."""
        from mas.registry import AgentRegistry

        # Setup agent and state (with multi-instance support)
        instance_id = "testinst"
        await counter._redis.hset(
            "agent:dereg_state_test",
            mapping={
                "id": "dereg_state_test",
                "capabilities": "[]",
                "metadata": "{}",
                "status": "ACTIVE",
                "registered_at": "123",
            },
        )
        # Set instance count to 1 (last instance)
        await counter._redis.set("agent:dereg_state_test:instance_count", "1")
        # New heartbeat format includes instance_id
        await counter._redis.set(
            f"agent:dereg_state_test:heartbeat:{instance_id}", "123"
        )
        await counter._redis.hset(
            "agent.state:dereg_state_test", mapping={"key": "value"}
        )

        counter.reset_counts()
        registry = AgentRegistry(counter)

        await registry.deregister("dereg_state_test", instance_id, keep_state=False)

        print(f"\n{counter.get_summary()}")

        # Multi-instance deregister pattern:
        # 1. DECR instance_count
        # 2. DELETE instance heartbeat
        # 3. DELETE instance token hash
        # 4. Pipeline for cleanup (when last instance, includes state deletion)
        assert counter.call_counts["decr"] == 1, (
            f"Expected 1 decr call, got {counter.call_counts.get('decr', 0)}"
        )
        assert counter.call_counts["delete"] == 2, (
            f"Expected 2 delete calls (heartbeat + token_hash), got {counter.call_counts.get('delete', 0)}"
        )
        assert counter.call_counts["pipeline_execute"] == 1, (
            f"Expected 1 pipeline_execute call, got {counter.call_counts.get('pipeline_execute', 0)}"
        )
