"""Performance benchmark tests.

These tests measure throughput and latency to validate optimization impact.
Run with verbose output to see benchmark results:

    uv run pytest tests/test_performance.py -v -s

Note: These tests require a running Redis instance on localhost:6379.
"""

import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

import pytest

pytestmark = pytest.mark.asyncio


@dataclass
class BenchmarkResult:
    """Results from a benchmark run."""

    name: str
    iterations: int
    total_time_seconds: float
    throughput_per_second: float
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    def __str__(self) -> str:
        return (
            f"\n{'=' * 60}\n"
            f"Benchmark: {self.name}\n"
            f"{'=' * 60}\n"
            f"  Iterations:    {self.iterations}\n"
            f"  Total time:    {self.total_time_seconds:.3f}s\n"
            f"  Throughput:    {self.throughput_per_second:.2f} ops/sec\n"
            f"  Avg latency:   {self.avg_latency_ms:.3f}ms\n"
            f"  Min latency:   {self.min_latency_ms:.3f}ms\n"
            f"  Max latency:   {self.max_latency_ms:.3f}ms\n"
            f"  P50 latency:   {self.p50_latency_ms:.3f}ms\n"
            f"  P95 latency:   {self.p95_latency_ms:.3f}ms\n"
            f"  P99 latency:   {self.p99_latency_ms:.3f}ms\n"
            f"{'=' * 60}"
        )


async def run_benchmark(
    name: str,
    func: Callable[[], Coroutine[Any, Any, Any]],
    iterations: int = 100,
    warmup: int = 10,
) -> BenchmarkResult:
    """
    Run a benchmark and collect timing statistics.

    Args:
        name: Name of the benchmark
        func: Async function to benchmark (no arguments)
        iterations: Number of iterations to run
        warmup: Number of warmup iterations (not counted)

    Returns:
        BenchmarkResult with timing statistics
    """
    # Warmup phase
    for _ in range(warmup):
        await func()

    # Benchmark phase
    latencies: list[float] = []
    start_total = time.perf_counter()

    for _ in range(iterations):
        start = time.perf_counter()
        await func()
        end = time.perf_counter()
        latencies.append((end - start) * 1000)  # Convert to ms

    end_total = time.perf_counter()
    total_time = end_total - start_total

    # Calculate statistics
    latencies.sort()
    p50_idx = int(len(latencies) * 0.50)
    p95_idx = int(len(latencies) * 0.95)
    p99_idx = int(len(latencies) * 0.99)

    return BenchmarkResult(
        name=name,
        iterations=iterations,
        total_time_seconds=total_time,
        throughput_per_second=iterations / total_time,
        avg_latency_ms=statistics.mean(latencies),
        min_latency_ms=min(latencies),
        max_latency_ms=max(latencies),
        p50_latency_ms=latencies[p50_idx],
        p95_latency_ms=latencies[p95_idx],
        p99_latency_ms=latencies[p99_idx],
    )


# -----------------------------------------------------------------------------
# Rate Limit Benchmarks
# -----------------------------------------------------------------------------


class TestRateLimitPerformance:
    """Benchmark rate limiting performance."""

    async def test_rate_limit_throughput(self, redis):
        """Measure rate limit checks per second."""
        from mas.gateway.rate_limit import RateLimitModule

        # High limits to avoid blocking during benchmark
        module = RateLimitModule(
            redis, default_per_minute=100000, default_per_hour=1000000
        )

        msg_counter = 0

        async def rate_limit_check():
            nonlocal msg_counter
            msg_counter += 1
            await module.check_rate_limit("bench_agent", f"msg_{msg_counter}")

        result = await run_benchmark(
            name="Rate Limit Check",
            func=rate_limit_check,
            iterations=200,
            warmup=20,
        )

        print(result)

        # Baseline expectations (adjust after optimization)
        # BEFORE optimization: ~100-500 ops/sec (limited by 10 Redis calls)
        # AFTER optimization: ~1000-5000 ops/sec (single Lua call)
        assert result.throughput_per_second > 50, (
            f"Throughput too low: {result.throughput_per_second:.2f} ops/sec"
        )
        assert result.p95_latency_ms < 100, (
            f"P95 latency too high: {result.p95_latency_ms:.3f}ms"
        )

    async def test_rate_limit_under_load(self, redis):
        """Test rate limit performance under concurrent load."""
        from mas.gateway.rate_limit import RateLimitModule

        module = RateLimitModule(
            redis, default_per_minute=100000, default_per_hour=1000000
        )

        async def check_for_agent(agent_id: str, count: int):
            for i in range(count):
                await module.check_rate_limit(agent_id, f"msg_{i}")

        # Simulate 5 agents making concurrent requests
        num_agents = 5
        requests_per_agent = 50

        start = time.perf_counter()
        await asyncio.gather(
            *[
                check_for_agent(f"agent_{i}", requests_per_agent)
                for i in range(num_agents)
            ]
        )
        elapsed = time.perf_counter() - start

        total_requests = num_agents * requests_per_agent
        throughput = total_requests / elapsed

        print("\nConcurrent Rate Limit Benchmark:")
        print(f"  Agents: {num_agents}")
        print(f"  Requests per agent: {requests_per_agent}")
        print(f"  Total requests: {total_requests}")
        print(f"  Total time: {elapsed:.3f}s")
        print(f"  Throughput: {throughput:.2f} ops/sec")

        assert throughput > 100, f"Concurrent throughput too low: {throughput:.2f}"


# -----------------------------------------------------------------------------
# Discovery Benchmarks
# -----------------------------------------------------------------------------


class TestDiscoveryPerformance:
    """Benchmark agent discovery performance."""

    @pytest.fixture
    async def setup_many_agents(self, redis):
        """Setup many agents for discovery benchmarking."""
        num_agents = 100
        agent_ids = [f"perf_agent_{i}" for i in range(num_agents)]

        for i, agent_id in enumerate(agent_ids):
            # Distribute capabilities across agents
            caps = ["common"]
            if i % 2 == 0:
                caps.append("even")
            if i % 3 == 0:
                caps.append("divisible_by_3")
            if i % 10 == 0:
                caps.append("divisible_by_10")

            await redis.hset(
                f"agent:{agent_id}",
                mapping={
                    "id": agent_id,
                    "capabilities": json.dumps(caps),
                    "metadata": json.dumps({"index": i}),
                    "status": "ACTIVE",
                    "registered_at": str(time.time()),
                },
            )

        yield num_agents

        # Cleanup
        for agent_id in agent_ids:
            await redis.delete(f"agent:{agent_id}")

    async def test_discovery_throughput(self, redis, setup_many_agents):
        """Measure discovery operations per second."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)
        num_agents = setup_many_agents

        async def discover_all():
            return await registry.discover()

        result = await run_benchmark(
            name=f"Discovery (all {num_agents} agents)",
            func=discover_all,
            iterations=50,
            warmup=5,
        )

        print(result)

        # Baseline expectations
        # BEFORE optimization: ~10-50 ops/sec (N+1 queries)
        # AFTER optimization: ~100-500 ops/sec (pipeline batching)
        assert result.throughput_per_second > 5, (
            f"Throughput too low: {result.throughput_per_second:.2f} ops/sec"
        )

    async def test_discovery_with_filter(self, redis, setup_many_agents):
        """Measure filtered discovery performance."""
        from mas.registry import AgentRegistry

        registry = AgentRegistry(redis)

        async def discover_filtered():
            return await registry.discover(capabilities=["divisible_by_10"])

        result = await run_benchmark(
            name="Discovery (filtered by capability)",
            func=discover_filtered,
            iterations=50,
            warmup=5,
        )

        print(result)

        # Filtered discovery should have similar performance characteristics
        # since filtering happens after fetching
        assert result.throughput_per_second > 5

    async def test_discovery_scaling(self, redis):
        """Test how discovery scales with number of agents."""
        from mas.registry import AgentRegistry

        results: list[tuple[int, float]] = []

        for num_agents in [10, 25, 50, 100]:
            # Setup agents
            agent_ids = [f"scale_agent_{i}" for i in range(num_agents)]
            for agent_id in agent_ids:
                await redis.hset(
                    f"agent:{agent_id}",
                    mapping={
                        "id": agent_id,
                        "capabilities": json.dumps(["scale_test"]),
                        "metadata": "{}",
                        "status": "ACTIVE",
                        "registered_at": "123",
                    },
                )

            registry = AgentRegistry(redis)

            # Measure time
            start = time.perf_counter()
            for _ in range(10):
                await registry.discover()
            elapsed = time.perf_counter() - start

            avg_time_ms = (elapsed / 10) * 1000
            results.append((num_agents, avg_time_ms))

            # Cleanup
            for agent_id in agent_ids:
                await redis.delete(f"agent:{agent_id}")

        print("\nDiscovery Scaling:")
        print(f"  {'Agents':<10} {'Avg Time (ms)':<15} {'Time/Agent (ms)':<15}")
        print(f"  {'-' * 40}")
        for num_agents, avg_time in results:
            per_agent = avg_time / num_agents
            print(f"  {num_agents:<10} {avg_time:<15.3f} {per_agent:<15.3f}")

        # After optimization, time/agent should be much lower due to batching
        # BEFORE: time scales linearly with N (O(N) queries)
        # AFTER: time is nearly constant (O(1) batched queries)


# -----------------------------------------------------------------------------
# Audit Benchmarks
# -----------------------------------------------------------------------------


class TestAuditPerformance:
    """Benchmark audit logging performance."""

    async def test_audit_log_throughput(self, redis):
        """Measure audit log writes per second."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)
        msg_counter = 0

        async def log_message():
            nonlocal msg_counter
            msg_counter += 1
            await audit.log_message(
                message_id=f"msg_{msg_counter}",
                sender_id="agent_a",
                target_id="agent_b",
                decision="ALLOWED",
                latency_ms=10.0,
                payload={"index": msg_counter},
            )

        result = await run_benchmark(
            name="Audit Log Write",
            func=log_message,
            iterations=100,
            warmup=10,
        )

        print(result)

        # Baseline expectations
        # BEFORE optimization: ~100-300 ops/sec (5 sequential calls)
        # AFTER optimization: ~500-1500 ops/sec (pipelined)
        assert result.throughput_per_second > 50, (
            f"Throughput too low: {result.throughput_per_second:.2f} ops/sec"
        )

    async def test_audit_query_throughput(self, redis):
        """Measure audit log query performance."""
        from mas.gateway.audit import AuditModule

        audit = AuditModule(redis)

        # Pre-populate audit log
        for i in range(50):
            await audit.log_message(
                message_id=f"query_msg_{i}",
                sender_id="query_agent",
                target_id="target_agent",
                decision="ALLOWED",
                latency_ms=10.0,
                payload={"index": i},
            )

        async def query_by_sender():
            return await audit.query_by_sender("query_agent", count=20)

        result = await run_benchmark(
            name="Audit Query by Sender",
            func=query_by_sender,
            iterations=50,
            warmup=5,
        )

        print(result)

        assert result.throughput_per_second > 20


# -----------------------------------------------------------------------------
# Circuit Breaker Benchmarks
# -----------------------------------------------------------------------------


class TestCircuitBreakerPerformance:
    """Benchmark circuit breaker performance."""

    async def test_circuit_check_throughput(self, redis):
        """Measure circuit check operations per second."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(redis)

        async def check_circuit():
            return await cb.check_circuit("cb_bench_target")

        result = await run_benchmark(
            name="Circuit Breaker Check",
            func=check_circuit,
            iterations=200,
            warmup=20,
        )

        print(result)

        assert result.throughput_per_second > 200

    async def test_circuit_check_and_record(self, redis):
        """Measure check + record cycle (common pattern)."""
        from mas.gateway.circuit_breaker import CircuitBreakerModule

        cb = CircuitBreakerModule(redis)
        counter = 0

        async def check_and_record():
            nonlocal counter
            counter += 1
            target = f"cb_cycle_target_{counter % 10}"
            status = await cb.check_circuit(target)
            if status.allowed:
                await cb.record_success(target)

        result = await run_benchmark(
            name="Circuit Breaker Check+Record Cycle",
            func=check_and_record,
            iterations=200,
            warmup=20,
        )

        print(result)

        # This pattern currently does 2 hgetall calls
        # Could be optimized to reuse state
        assert result.throughput_per_second > 100


# -----------------------------------------------------------------------------
# End-to-End Gateway Benchmarks
# -----------------------------------------------------------------------------


class TestGatewayPerformance:
    """Benchmark complete gateway message handling."""

    async def test_gateway_message_throughput(self, redis):
        """Measure end-to-end gateway message processing."""
        from mas.gateway import GatewayService
        from mas.gateway.config import FeaturesSettings, GatewaySettings
        from mas.protocol import EnvelopeMessage, MessageMeta

        # Configure gateway with minimal features for baseline
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=False,
                rbac=False,
                message_signing=False,
                circuit_breaker=False,
            ),
        )

        gateway = GatewayService(settings=settings)
        await gateway.start()

        try:
            # Setup sender and target
            sender_id = "gw_bench_sender"
            target_id = "gw_bench_target"
            token = "bench_token"
            instance_id = "inst_1234"

            await redis.hset(f"agent:{sender_id}", mapping={"status": "ACTIVE"})
            await redis.set(
                f"agent:{sender_id}:token_hash:{instance_id}",
                hashlib.sha256(token.encode()).hexdigest(),
            )
            await redis.hset(f"agent:{target_id}", mapping={"status": "ACTIVE"})
            await gateway.authz.set_permissions(sender_id, allowed_targets=[target_id])

            msg_counter = 0

            async def handle_message():
                nonlocal msg_counter
                msg_counter += 1
                message = EnvelopeMessage(
                    sender_id=sender_id,
                    target_id=target_id,
                    message_type="benchmark.message",
                    data={"index": msg_counter},
                    meta=MessageMeta(sender_instance_id=instance_id),
                )
                return await gateway.handle_message(message, token)

            result = await run_benchmark(
                name="Gateway Message Handling (minimal features)",
                func=handle_message,
                iterations=100,
                warmup=10,
            )

            print(result)

            # Gateway should handle messages reasonably fast
            assert result.throughput_per_second > 20

        finally:
            await gateway.stop()

    async def test_gateway_with_all_features(self, redis):
        """Measure gateway performance with all features enabled."""
        from mas.gateway import GatewayService
        from mas.gateway.config import FeaturesSettings, GatewaySettings
        from mas.protocol import EnvelopeMessage, MessageMeta

        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=True,
                rbac=True,
                message_signing=False,  # Requires key setup
                circuit_breaker=True,
            ),
        )

        gateway = GatewayService(settings=settings)
        await gateway.start()

        try:
            sender_id = "gw_full_sender"
            target_id = "gw_full_target"
            token = "full_token"
            instance_id = "inst_1234"

            await redis.hset(f"agent:{sender_id}", mapping={"status": "ACTIVE"})
            await redis.set(
                f"agent:{sender_id}:token_hash:{instance_id}",
                hashlib.sha256(token.encode()).hexdigest(),
            )
            await redis.hset(f"agent:{target_id}", mapping={"status": "ACTIVE"})
            await gateway.authz.set_permissions(sender_id, allowed_targets=[target_id])

            msg_counter = 0

            async def handle_message():
                nonlocal msg_counter
                msg_counter += 1
                message = EnvelopeMessage(
                    sender_id=sender_id,
                    target_id=target_id,
                    message_type="benchmark.message",
                    data={"text": f"Message {msg_counter}", "count": msg_counter},
                    meta=MessageMeta(sender_instance_id=instance_id),
                )
                return await gateway.handle_message(message, token)

            result = await run_benchmark(
                name="Gateway Message Handling (DLP + RBAC + Circuit Breaker)",
                func=handle_message,
                iterations=100,
                warmup=10,
            )

            print(result)

            # With all features, expect lower but still reasonable throughput
            assert result.throughput_per_second > 10

        finally:
            await gateway.stop()
