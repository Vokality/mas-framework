"""Shared fleet retention, parent joins and failover against actual Redis."""

from __future__ import annotations

import gc
from unittest.mock import patch
from weakref import ref

import pytest
from mas_core.observability import (
    BrokerCounters,
    BrokerObservation,
    BrokerSession,
    CorrelationNode,
    ExportHealth,
    FleetMember,
    ObservabilityStore,
    ObservationSettings,
    ObservedSpan,
    PerformancePoint,
)
from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.typing import EncodableT, FieldT


def heartbeat(
    broker: str,
    at: float,
    count: int = 0,
    sequence: int = 1,
    incarnation: str = "instance",
    started: float = 900,
) -> BrokerObservation:
    return BrokerObservation(
        broker_id=broker,
        instance_id=incarnation,
        sequence=sequence,
        observed_at=at,
        started_at=started,
        status="healthy",
        grpc_address="localhost:50051",
        redis_available=True,
        counters=BrokerCounters(accepted_messages=count),
    )


def span(
    identity: int,
    name: str,
    at: float,
    parent: int | None = None,
    message: str = "message",
    reply: bool = False,
    trace_id: int = 100,
) -> ObservedSpan:
    return ObservedSpan(
        trace_id=f"{trace_id:032x}",
        span_id=f"{identity:016x}",
        parent_span_id=f"{parent:016x}" if parent is not None else None,
        name=name,
        service_name="mas-agent",
        started_unix_ns=int(at * 1e9),
        finished_unix_ns=int((at + 0.01) * 1e9),
        attributes={"mas.message_id": message, "mas.is_reply": reply},
    )


async def test_correlation_cache_releases_full_models_and_preserves_join() -> None:
    redis = Redis.from_url("redis://127.0.0.1:6379", decode_responses=True)
    store = ObservabilityStore(redis)
    spans = [
        span(1, "mas.agent.send", 1000, trace_id=1),
        span(2, "mas.rpc.send", 1000.01, 1, trace_id=1),
        span(3, "mas.server.ingress.send", 1000.02, 2, trace_id=1),
        span(4, "mas.agent.handle_message", 1000.2, 3, trace_id=1),
    ]
    models = [ref(observed) for observed in spans]
    try:
        for observed in spans:
            assert store._remember(observed)
        del observed, spans
        gc.collect()
        assert all(model() is None for model in models)
        endpoint = store._spans[f"{1:032x}:{4:016x}"]
        assert isinstance(endpoint, CorrelationNode)
        assert endpoint.message_id == "message"
        assert not hasattr(endpoint, "__dict__")
        assert store._latency(endpoint) == 200
    finally:
        await redis.aclose()


async def test_compact_pending_reply_restores_before_late_parents(redis: Redis) -> None:
    original = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await original.publish_broker(heartbeat("a", 1000))
        await original.flush()
    reply = span(
        4,
        "mas.agent.transport.receive",
        1000.2,
        3,
        message="reply",
        reply=True,
        trace_id=1,
    )
    with patch("mas_core.observability.time.time", return_value=1001):
        await original.publish_broker(heartbeat("a", 1001, 1, 2))
        await original.ingest_spans([reply])
        await original.flush()
        identity = f"{1:032x}:{4:016x}"
        assert isinstance(original._pending[identity], CorrelationNode)
        assert original._pending[identity].message_id == "reply"
        assert (await original.fleet()).performance.latency_samples == 0
        await redis.delete("mas:observability:aggregator")
        successor = ObservabilityStore(redis)
        await successor.flush()
        assert successor._pending[identity] == original._pending[identity]
        await successor.ingest_spans(
            [
                span(1, "mas.agent.reply", 1000, message="reply", trace_id=1),
                span(2, "mas.rpc.reply", 1000.01, 1, message="reply", trace_id=1),
                span(
                    3,
                    "mas.server.ingress.reply",
                    1000.02,
                    2,
                    message="reply",
                    trace_id=1,
                ),
            ]
        )
        await successor.flush()
        performance = (await successor.fleet()).performance
        assert performance.accepted_messages == performance.latency_samples == 1
        assert performance.end_to_end_p95_ms == 200
        assert performance.complete
        assert not successor._pending
        # Correlation projection does not replace the durable full observations.
        rows = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("mas:observability:spans")
        )
        retained = [
            observed
            for _cursor, fields in rows
            for observed in TypeAdapter(list[ObservedSpan]).validate_json(
                fields["data"]
            )
        ]
        assert retained[0] == reply
        assert retained[0].attributes["mas.is_reply"] is True


async def test_sequenced_fleet_rates_and_browser_independent_history(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.publish_broker(heartbeat("b", 1000))
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 500, 2))
        await store.publish_broker(heartbeat("b", 1001, 500, 2))
        await store.publish_broker(heartbeat("a", 999, 999_999, 1))
        await store.flush()
        reader = ObservabilityStore(redis)
        fleet = await reader.fleet()
        assert len(fleet.brokers) == 2
        assert fleet.performance.accepted_rate == 1000
        history = await reader.history()
        assert history[-1].accepted_messages == 1000
        assert history[-1].accepted_rate == 1000
        assert not fleet.performance.complete


async def test_rolling_rate_retains_interval_start(redis: Redis) -> None:
    store = ObservabilityStore(redis)
    for second in range(65):
        with patch("mas_core.observability.time.time", return_value=1000 + second):
            await store.publish_broker(
                heartbeat("a", 1000 + second, second * 1000, second + 1)
            )
            await store.flush()
    with patch("mas_core.observability.time.time", return_value=1064):
        fleet = await store.fleet()
        assert fleet.performance.accepted_rate == 1000
        assert fleet.performance.accepted_messages == 60_000
        assert fleet.performance.started_at == 1004
        assert not any(alert.kind == "throughput_slo" for alert in fleet.alerts)


async def test_out_of_order_trace_join_and_duplicate_delivery(redis: Redis) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 1, 2))
        await store.ingest_spans([span(3, "mas.agent.handle_message", 1000.2, 2)])
        await store.flush()
        assert (await store.fleet()).performance.latency_samples == 0
        await store.ingest_spans(
            [
                span(2, "mas.server.ingress.send", 1000.01, 1),
                span(1, "mas.agent.send", 1000),
                span(4, "mas.agent.handle_message", 1000.4, 2),
            ]
        )
        await store.flush()
        fleet = await store.fleet()
        assert fleet.performance.latency_samples == 1
        assert fleet.performance.end_to_end_p95_ms == 200
        assert fleet.performance.latency_coverage == 1
        assert fleet.performance.complete
        detail = await store.trace(f"{100:032x}")
        assert detail is not None
        assert detail.summary.span_count == 4
        assert detail.spans[-1].depth == 2
        assert len(await store.history()) == 2  # two wall-clock seconds


async def test_raw_retention_keeps_unsampled_joins_and_sampled_optional_detail(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis, ObservationSettings(trace_sample_every=100))
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
    unsampled = [
        span(1, "mas.agent.send", 1000, message="unsampled", trace_id=1),
        span(2, "mas.rpc.send", 1000.01, 1, message="unsampled", trace_id=1),
        span(3, "mas.server.ingress.send", 1000.02, 2, message="unsampled", trace_id=1),
        span(
            4, "mas.server.policy.ingest", 1000.03, 3, message="unsampled", trace_id=1
        ),
        span(5, "mas.agent.handle_message", 1000.1, 3, message="unsampled", trace_id=1),
    ]
    sampled = [
        span(11, "mas.agent.send", 1000, message="sampled"),
        span(12, "mas.rpc.send", 1000.01, 11, message="sampled"),
        span(13, "mas.server.ingress.send", 1000.02, 12, message="sampled"),
        span(14, "mas.server.policy.ingest", 1000.03, 13, message="sampled"),
        span(15, "mas.agent.handle_message", 1000.2, 13, message="sampled"),
    ]
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 2, 2))
        await store.ingest_spans(unsampled + sampled)
        rows = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("mas:observability:spans")
        )
        raw = [
            item
            for _identity, fields in rows
            for item in TypeAdapter(list[ObservedSpan]).validate_json(fields["data"])
        ]
        assert {item.name for item in raw if item.trace_id == f"{1:032x}"} == {
            "mas.agent.send",
            "mas.rpc.send",
            "mas.server.ingress.send",
            "mas.agent.handle_message",
        }
        assert {item.name for item in raw if item.trace_id == f"{100:032x}"} == {
            item.name for item in sampled
        }
        await store.flush()
        performance = (await store.fleet()).performance
        assert performance.accepted_messages == performance.latency_samples == 2
        assert performance.latency_coverage == 1 and performance.complete
        assert performance.end_to_end_p95_ms == 200
        detail = await store.trace(f"{100:032x}")
        assert detail is not None and detail.summary.complete
        assert detail.summary.span_count == len(sampled)


async def test_aggregator_failover_preserves_trace_parents_and_histogram(
    redis: Redis,
) -> None:
    old = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await old.publish_broker(heartbeat("a", 1000))
        await old.ingest_spans([span(1, "mas.agent.send", 1000)])
        await old.flush()
        # Simulate expiry rather than waiting; successor owns the real Redis lease.
        await redis.delete("mas:observability:aggregator")
        successor = ObservabilityStore(redis)
        await successor.ingest_spans(
            [
                span(2, "mas.server.ingress.send", 1000.01, 1),
                span(3, "mas.agent.handle_message", 1000.1, 2),
            ]
        )
        await successor.flush()
        await old.flush()  # former owner cannot overwrite the successor
        detail = await successor.trace(f"{100:032x}")
        assert detail is not None
        assert detail.summary.span_count == 3
        assert detail.summary.end_to_end_ms == 100
        assert (await successor.fleet()).performance.latency_samples == 1
        await successor.ingest_spans([span(3, "mas.agent.handle_message", 1000.1, 2)])
        await successor.flush()
        assert (await successor.fleet()).performance.latency_samples == 1


async def test_broker_restart_resets_baseline_without_counting_prior_lifetime(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000, 100))
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(
            heartbeat("a", 1001, 1000, incarnation="new", started=1001)
        )
        await store.publish_broker(heartbeat("a", 1001, 99999, sequence=99))
        await store.flush()
        fleet = await store.fleet()
        assert fleet.brokers[0].observation.instance_id == "new"
        assert fleet.performance.accepted_messages == 0
        assert not fleet.performance.complete


async def test_freshness_and_export_failures_resolve_and_idle_is_not_capacity_failure(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis)
    failed = heartbeat("a", 1000).model_copy(
        update={
            "exporters": [
                ExportHealth(
                    signal="traces",
                    configured=True,
                    attempts=1,
                    failures=1,
                    status="degraded",
                    last_error="HTTP 503",
                )
            ]
        }
    )
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(failed)
        await store.flush()
        assert any(
            alert.kind == "export_failure" for alert in (await store.fleet()).alerts
        )
    with patch("mas_core.observability.time.time", return_value=1006):
        stale = await store.fleet()
        assert stale.brokers[0].status == "stale"
        assert not stale.complete
        assert any(alert.kind == "broker_stale" for alert in stale.alerts)
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1007):
        await store.publish_broker(heartbeat("a", 1007, sequence=2))
        await store.flush()
        healed = await store.fleet()
        assert any(
            alert.kind == "broker_stale" and alert.status == "resolved"
            for alert in healed.alerts
        )
        assert not any(alert.kind == "throughput_slo" for alert in healed.alerts)


async def test_reply_receipt_clock_skew_and_missing_parents(redis: Redis) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.ingest_spans(
            [
                span(1, "mas.agent.reply", 1000),
                span(2, "mas.server.ingress.reply", 1000.01, 1),
                span(3, "mas.agent.transport.receive", 1000.05, 2, reply=True),
            ]
        )
        await store.flush()
        assert (await store.fleet()).performance.end_to_end_p95_ms == 50
        detail = await store.trace(f"{100:032x}")
        assert detail is not None and detail.summary.complete
        skew = store._detail(
            [
                span(1, "mas.agent.send", 1000.2),
                span(2, "mas.agent.handle_message", 1000, 1),
            ]
        )
        assert skew.summary.clock_skew_detected
        assert not skew.summary.complete
        partial = store._detail([span(2, "mas.agent.handle_message", 1000, 9)])
        assert not partial.summary.complete
        assert partial.summary.end_to_end_ms is None


async def test_expired_history_and_trace_details_are_not_returned(redis: Redis) -> None:
    settings = ObservationSettings(retention_seconds=60, history_limit=60)
    store = ObservabilityStore(redis, settings)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.ingest_spans([span(1, "mas.agent.send", 1000)])
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1061):
        assert await store.history() == []
        assert await store.traces() == []
        assert await store.trace(f"{100:032x}") is None
        assert (await store.fleet()).performance.end_to_end_p95_ms is None


def test_observation_boundaries_reject_invalid_clock_size_and_freshness() -> None:
    with pytest.raises(ValidationError, match="end precedes"):
        span(1, "mas.agent.send", 1000).model_copy().model_validate(
            {
                **span(1, "mas.agent.send", 1000).model_dump(),
                "finished_unix_ns": 0,
            }
        )
    with pytest.raises(ValidationError):
        ObservationSettings(heartbeat_seconds=5, stale_after_seconds=5)
    with pytest.raises(ValidationError):
        BrokerSession(
            agent_id="a", instance_id="i", inflight=-1, outbound=0, worker_running=True
        )


async def test_failed_flush_retries_from_committed_cursor(redis: Redis) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001))
        await store.flush()
        await store.ingest_spans(
            [
                span(1, "mas.agent.send", 1000),
                span(2, "mas.server.ingress.send", 1000.01, 1),
                span(3, "mas.agent.handle_message", 1000.1, 2),
            ]
        )
        before = await redis.get("mas:observability:checkpoint")
        await redis.delete("mas:observability:history")
        await redis.set("mas:observability:history", "wrong type")
        with pytest.raises(Exception, match="Invalid observation checkpoint type"):
            await store.flush()
        assert await redis.get("mas:observability:checkpoint") == before
        await redis.delete("mas:observability:history")
        await store.flush()
        assert (await store.fleet()).performance.latency_samples == 1
        assert await store.trace(f"{100:032x}") is not None


async def test_bounded_registry_exposes_rejected_broker_coverage(redis: Redis) -> None:
    store = ObservabilityStore(redis, ObservationSettings(broker_limit=1))
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        with pytest.raises(ValueError, match="capacity"):
            await store.publish_broker(heartbeat("b", 1000))
        await store.flush()
        fleet = await store.fleet()
        assert not fleet.complete
        assert fleet.status == "degraded"
        assert any(
            alert.alert_id == "coverage_gap:fleet_registry" for alert in fleet.alerts
        )


async def test_missing_identity_never_counts_as_proven_delivery(redis: Redis) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 1, 2))
        await store.ingest_spans(
            [
                span(1, "mas.agent.send", 1000),
                span(2, "mas.agent.handle_message", 1000.1, 1).model_copy(
                    update={"attributes": {}}
                ),
            ]
        )
        await store.flush()
        fleet = await store.fleet()
        assert fleet.performance.latency_samples == 0
        assert not fleet.performance.complete
        assert any(
            alert.alert_id == "coverage_gap:aggregator" for alert in fleet.alerts
        )


async def test_loss_of_samples_cannot_resolve_previous_latency_breach(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.ingest_spans(
            [
                span(1, "mas.agent.send", 999),
                span(2, "mas.agent.handle_message", 999.5, 1),
            ]
        )
        await store.flush()
        assert any(
            alert.alert_id == "latency_slo" and alert.status == "active"
            for alert in (await store.fleet()).alerts
        )
    with patch("mas_core.observability.time.time", return_value=1061):
        await store.publish_broker(heartbeat("a", 1061, sequence=2))
        await store.flush()
        fleet = await store.fleet()
        assert fleet.performance.end_to_end_p95_ms is None
        assert any(
            alert.alert_id == "latency_slo" and alert.status == "active"
            for alert in fleet.alerts
        )
        assert await store.history(limit=500)
        assert len(await store.traces(limit=500)) == 1


async def test_partial_export_batches_retain_early_parent_for_late_delivery(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis, ObservationSettings(max_pending_spans=4096))
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
        for batch in range(6):
            await store.ingest_spans(
                [
                    span(
                        identity,
                        "mas.agent.send" if identity == 1 else "mas.server.policy",
                        1000,
                    )
                    for identity in range(batch * 512 + 1, (batch + 1) * 512 + 1)
                ]
            )
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 1, 2))
        await store.ingest_spans([span(4000, "mas.agent.handle_message", 1000.2, 1)])
        await store.flush()
        point = (await store.fleet()).performance
        assert point.latency_samples == 1
        assert point.end_to_end_p95_ms == 200
        assert point.latency_coverage == 1
        assert point.complete


async def test_span_retention_counts_actual_variable_batch_sizes(redis: Redis) -> None:
    limit = 4096
    store = ObservabilityStore(redis, ObservationSettings(max_pending_spans=limit))
    with patch("mas_core.observability.time.time", return_value=1000):
        for batch in range(10):
            await store.ingest_spans(
                [
                    span(identity, "mas.server.policy", 1000)
                    for identity in range(batch * 512 + 1, (batch + 1) * 512 + 1)
                ]
            )
        await store.ingest_spans([span(5121, "mas.server.policy", 1000)])
        rows = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("mas:observability:spans")
        )
        retained = [
            observed
            for _, fields in rows
            for observed in TypeAdapter(list[ObservedSpan]).validate_json(
                fields["data"]
            )
        ]
        # Whole oldest chunks may be dropped; the retained budget uses span counts.
        assert limit - 512 < len(retained) <= limit
        assert retained[-1].span_id == f"{5121:016x}"
        assert retained[0].span_id != f"{1:016x}"


@pytest.mark.parametrize(
    (
        "rate",
        "duration",
        "counter_complete",
        "latency_complete",
        "fresh",
        "resolved",
    ),
    [
        (1200, 60, True, True, True, True),
        (1200, 60, True, False, True, True),
        (1000, 60, True, True, True, True),
        (1200, 59, True, True, True, False),
        (1200, 60, False, False, True, False),
        (None, 60, False, False, True, False),
        (800, 60, True, True, True, False),
        (1200, 60, True, True, False, False),
    ],
)
async def test_throughput_recovery_requires_valid_full_window_with_backlog(
    redis: Redis,
    rate: float | None,
    duration: float,
    counter_complete: bool,
    latency_complete: bool,
    fresh: bool,
    resolved: bool,
) -> None:
    store = ObservabilityStore(redis)
    observation = heartbeat("a", 1060).model_copy(
        update={
            "sessions": [
                BrokerSession(
                    agent_id="worker",
                    instance_id="lease",
                    inflight=0,
                    outbound=8,
                    worker_running=True,
                )
            ]
        }
    )
    member = FleetMember(
        observation=observation, fresh=True, age_seconds=0, status="healthy"
    )
    previous_point = PerformancePoint(
        started_at=1000,
        finished_at=1060,
        accepted_messages=48_000,
        rejected_messages=12_000,
        accepted_rate=800,
        latency_samples=48_000,
        latency_coverage=1,
        end_to_end_p95_ms=100,
        complete=True,
        counter_complete=True,
    )
    previous = store._alerts([member], previous_point, [], 1060)
    assert any(alert.alert_id == "throughput_slo" for alert in previous)
    current_point = previous_point.model_copy(
        update={
            "started_at": 1061 - duration,
            "finished_at": 1061,
            "accepted_messages": int((rate or 0) * duration),
            "rejected_messages": (
                int(max(0, 1000 - rate) * duration) if rate is not None else 0
            ),
            "accepted_rate": rate,
            "latency_samples": int((rate or 0) * duration) if latency_complete else 0,
            "latency_coverage": 1 if latency_complete else 0,
            "end_to_end_p95_ms": 100 if latency_complete else None,
            "complete": counter_complete and latency_complete,
            "counter_complete": counter_complete,
        }
    )
    current_member = member.model_copy(
        update={
            "fresh": fresh,
            "age_seconds": 0 if fresh else 6,
            "status": "healthy" if fresh else "stale",
        }
    )
    alerts = store._alerts([current_member], current_point, previous, 1061)
    throughput = next(alert for alert in alerts if alert.alert_id == "throughput_slo")
    assert (throughput.status == "resolved") is resolved
    assert throughput.opened_at == 1060
    assert throughput.resolved_at == (1061 if resolved else None)


async def test_counter_restart_keeps_window_partial_despite_equal_retained_counts(
    redis: Redis,
) -> None:
    store = ObservabilityStore(redis)
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 1, 2))
        await store.ingest_spans(
            [
                span(1, "mas.agent.send", 1000, message="first"),
                span(2, "mas.agent.handle_message", 1000.1, 1, message="first"),
            ]
        )
        await store.flush()
        assert (await store.fleet()).performance.complete
    with patch("mas_core.observability.time.time", return_value=1002):
        await store.publish_broker(
            heartbeat("a", 1002, incarnation="restarted", started=1002)
        )
        await store.flush()
    with patch("mas_core.observability.time.time", return_value=1003):
        await store.publish_broker(
            heartbeat("a", 1003, 1, 2, incarnation="restarted", started=1002)
        )
        await store.ingest_spans(
            [
                span(3, "mas.agent.send", 1002, message="second"),
                span(4, "mas.agent.handle_message", 1002.1, 3, message="second"),
            ]
        )
        await store.flush()
        fleet = await store.fleet()
        assert fleet.performance.accepted_messages == 2
        assert fleet.performance.latency_samples == 2
        assert fleet.performance.latency_coverage is None
        assert fleet.performance.end_to_end_p95_ms == 100
        assert not fleet.performance.complete
        assert not fleet.performance.counter_complete
        assert not (await store.history())[-1].complete


@pytest.mark.parametrize("counter", ["invalid", "-1", "1.5", "1001"])
async def test_malformed_span_counter_cannot_partially_append(
    redis: Redis, counter: str
) -> None:
    store = ObservabilityStore(redis, ObservationSettings(max_pending_spans=1000))
    await store.ingest_spans([span(1, "mas.agent.send", 1000)])
    await redis.set("mas:observability:span_count", counter)
    before = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
        await redis.xrange("mas:observability:spans")
    )
    with pytest.raises(ResponseError, match="Invalid span journal counter"):
        await store.ingest_spans([span(2, "mas.agent.handle_message", 1000.1, 1)])
    after = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
        await redis.xrange("mas:observability:spans")
    )
    assert after == before
    assert await redis.get("mas:observability:span_count") == counter


@pytest.mark.parametrize("count", [None, "invalid", "0", "-1", "0.5", "2049"])
async def test_invalid_retained_chunk_cannot_partially_append_or_trim(
    redis: Redis, count: str | None
) -> None:
    store = ObservabilityStore(redis, ObservationSettings(max_pending_spans=1000))
    fields: dict[FieldT, EncodableT] = {
        "data": TypeAdapter(list[ObservedSpan])
        .dump_json([span(1, "mas.agent.send", 1000)])
        .decode(),
    }
    if count is not None:
        fields["count"] = count
    await redis.xadd("mas:observability:spans", fields)
    await redis.set("mas:observability:span_count", 1000)
    before = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
        await redis.xrange("mas:observability:spans")
    )
    with pytest.raises(ResponseError, match="Invalid span journal record"):
        await store.ingest_spans([span(2, "mas.agent.handle_message", 1000.1, 1)])
    after = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
        await redis.xrange("mas:observability:spans")
    )
    assert after == before
    assert await redis.get("mas:observability:span_count") == "1000"


async def test_minimum_span_budget_keeps_tail_of_larger_export(redis: Redis) -> None:
    store = ObservabilityStore(redis, ObservationSettings(max_pending_spans=1000))
    with patch("mas_core.observability.time.time", return_value=1000):
        await store.publish_broker(heartbeat("a", 1000))
        await store.flush()
        await store.ingest_spans(
            [
                span(
                    identity,
                    "mas.agent.send" if identity == 2048 else "mas.server.policy",
                    1000,
                )
                for identity in range(1, 2049)
            ]
        )
        rows = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("mas:observability:spans")
        )
        retained = [
            observed
            for _, fields in rows
            for observed in TypeAdapter(list[ObservedSpan]).validate_json(
                fields["data"]
            )
        ]
        assert 0 < len(retained) <= 1000
        assert retained[-1].span_id == f"{2048:016x}"
        assert await redis.get("mas:observability:span_count") == str(len(retained))
    with patch("mas_core.observability.time.time", return_value=1001):
        await store.publish_broker(heartbeat("a", 1001, 1, 2))
        await store.ingest_spans([span(2049, "mas.agent.handle_message", 1000.1, 2048)])
        await store.flush()
        point = (await store.fleet()).performance
        assert point.latency_samples == 1
        assert point.end_to_end_p95_ms == 100
        assert point.complete
