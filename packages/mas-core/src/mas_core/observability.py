"""Bounded, shared runtime observations for fleet operations and tracing."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from redis.asyncio import Redis

type ObservationAttribute = str | int | float | bool
type BrokerStatus = Literal["healthy", "degraded", "stopped", "unknown"]


class ObservationModel(BaseModel):
    """Validate persisted and received observations at their boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ObservedSpan(ObservationModel):
    """Safe span metadata, without business payloads, headers or exceptions."""

    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    name: str = Field(min_length=1, max_length=200)
    service_name: str = Field(min_length=1, max_length=200)
    started_unix_ns: int = Field(ge=0)
    finished_unix_ns: int = Field(ge=0)
    failed: bool = False
    attributes: dict[str, ObservationAttribute] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        """Reject invalid clocks and unbounded metadata at ingestion."""
        if self.finished_unix_ns < self.started_unix_ns:
            raise ValueError("Span end precedes its start")
        if len(self.attributes) > 64:
            raise ValueError("At most 64 safe span attributes are retained")
        for key, value in self.attributes.items():
            if len(key) > 100 or (isinstance(value, str) and len(value) > 512):
                raise ValueError("Span metadata exceeds its size limit")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Span metadata must be finite")
        return self


@dataclass(frozen=True, slots=True)
class CorrelationNode:
    """Scalar ancestry and delivery identity retained for exact latency joins."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    started_unix_ns: int
    message_id: str | None


class ExportHealth(ObservationModel):
    """Actual exporter outcomes for one telemetry signal."""

    signal: Literal["traces", "metrics"]
    configured: bool
    attempts: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    exported_items: int = Field(default=0, ge=0)
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_error: str | None = Field(default=None, max_length=100)
    age_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    status: Literal["disabled", "pending", "healthy", "degraded", "stale"] = "pending"


class SloTargets(ObservationModel):
    """The user's capacity and delivery latency targets."""

    accepted_rate: float = Field(default=1000, gt=0, allow_inf_nan=False)
    end_to_end_p95_ms: float = Field(default=300, gt=0, allow_inf_nan=False)


class ObservationSettings(ObservationModel):
    """Explicit limits for shared telemetry collection and retention."""

    heartbeat_seconds: float = Field(default=1, gt=0, le=30, allow_inf_nan=False)
    stale_after_seconds: float = Field(default=5, gt=0, le=300, allow_inf_nan=False)
    retention_seconds: int = Field(default=3600, ge=60, le=86_400)
    history_limit: int = Field(default=3600, ge=60, le=86_400)
    trace_limit: int = Field(default=10_000, ge=100, le=100_000)
    max_pending_spans: int = Field(default=120_000, ge=1000, le=500_000)
    latency_window_seconds: int = Field(default=60, ge=5, le=300)
    targets: SloTargets = Field(default_factory=SloTargets)
    trace_sample_every: int = Field(default=100, ge=1, le=10_000)
    broker_limit: int = Field(default=500, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_freshness(self) -> Self:
        """Allow at least one full heartbeat period before marking stale."""
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError("Stale interval must exceed the heartbeat interval")
        return self

    def retains_span(
        self, name: str, trace_id: int, *, is_reply: bool, failed: bool
    ) -> bool:
        """Keep every delivery join and sampled detail without changing exports."""
        return (
            failed
            or (trace_id & 0xFFFFFFFF) % self.trace_sample_every == 0
            or name
            in {
                "mas.agent.send",
                "mas.agent.request",
                "mas.agent.reply",
                "mas.rpc.send",
                "mas.rpc.request",
                "mas.rpc.reply",
                "mas.server.ingress.send",
                "mas.server.ingress.request",
                "mas.server.ingress.reply",
                "mas.agent.handle_message",
            }
            or (name == "mas.agent.transport.receive" and is_reply)
        )


class BrokerCounters(ObservationModel):
    """Counters attributable to one broker incarnation."""

    accepted_messages: int = Field(default=0, ge=0)
    rejected_messages: int = Field(default=0, ge=0)
    delivery_acks: int = Field(default=0, ge=0)
    delivery_nacks: int = Field(default=0, ge=0)
    redis_errors: int = Field(default=0, ge=0)
    dropped_spans: int = Field(default=0, ge=0)
    scope_complete: bool = True
    dropped_scope_updates: int = Field(default=0, ge=0)


class BrokerSession(ObservationModel):
    """Agent transport and worker state on a fleet member."""

    agent_id: str
    instance_id: str
    inflight: int = Field(ge=0)
    outbound: int = Field(ge=0)
    worker_running: bool


class BrokerObservation(ObservationModel):
    """A sequenced heartbeat from a specific broker incarnation."""

    broker_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    instance_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    observed_at: float = Field(ge=0, allow_inf_nan=False)
    started_at: float = Field(ge=0, allow_inf_nan=False)
    status: BrokerStatus
    grpc_address: str
    management_url: str | None = None
    redis_available: bool
    redis_latency_ms: float | None = None
    issues: list[str] = Field(default_factory=list)
    sessions: list[BrokerSession] = Field(default_factory=list)
    counters: BrokerCounters = Field(default_factory=BrokerCounters)
    exporters: list[ExportHealth] = Field(default_factory=list)


class FleetMember(ObservationModel):
    """A broker heartbeat with server-evaluated freshness."""

    observation: BrokerObservation
    fresh: bool
    age_seconds: float
    status: Literal["healthy", "degraded", "stopped", "unknown", "stale"]


class PerformancePoint(ObservationModel):
    """Measured fleet traffic and paired delivery timings for an interval."""

    started_at: float
    finished_at: float
    accepted_messages: int = Field(default=0, ge=0)
    rejected_messages: int = Field(default=0, ge=0)
    accepted_rate: float | None = None
    end_to_end_p95_ms: float | None = None
    latency_samples: int = Field(default=0, ge=0)
    latency_coverage: float | None = None
    delivery_acks: int = Field(default=0, ge=0)
    delivery_nacks: int = Field(default=0, ge=0)
    redis_errors: int = Field(default=0, ge=0)
    complete: bool = False
    counter_complete: bool = False
    clock_scope: str = (
        "Rolling delivery window: client send/request/reply span start to handler "
        "entry or validated reply receipt. P95 uses 1ms upper-bound bins. "
        "Cross-process "
        "wall clocks require synchronization. Application scheduling before the "
        "send API is outside this interval."
    )


class OperationalAlert(ObservationModel):
    """An active or resolved operational condition, without notifications."""

    alert_id: str
    kind: Literal[
        "broker_health",
        "broker_stale",
        "export_failure",
        "latency_slo",
        "throughput_slo",
        "coverage_gap",
    ]
    severity: Literal["critical", "warning", "info"]
    status: Literal["active", "resolved"]
    title: str
    detail: str
    opened_at: float
    updated_at: float
    resolved_at: float | None = None


class FleetSnapshot(ObservationModel):
    """A render-ready fleet view including retained conditions and SLO scope."""

    generated_at: float
    status: BrokerStatus
    complete: bool
    brokers: list[FleetMember]
    performance: PerformancePoint
    targets: SloTargets
    alerts: list[OperationalAlert]
    retention_seconds: int
    trace_limit: int
    stale_after_seconds: float
    trace_sample_every: int
    latency_window_seconds: int
    scope: str = (
        "Broker heartbeats, accepted-message counters and retained observations "
        "are shared through Redis. Missing or stale members reduce coverage. "
        "Trace details retain a deterministic sample plus errors, bounded by count "
        "and time. Latency includes all successfully correlated retained deliveries; "
        "coverage can lag while exported parent spans arrive."
    )


class TraceSummary(ObservationModel):
    """Payload-free trace identity and measured span coverage."""

    trace_id: str
    started_at: float
    finished_at: float
    span_count: int
    error_count: int
    message_ids: list[str]
    services: list[str]
    end_to_end_ms: float | None
    complete: bool
    clock_skew_detected: bool


class TraceSpan(ObservationModel):
    """One span with backend-computed order, nesting and waterfall geometry."""

    span: ObservedSpan
    depth: int
    offset_ms: float
    duration_ms: float


class TraceDetail(ObservationModel):
    """A bounded trace waterfall prepared for direct presentation."""

    summary: TraceSummary
    spans: list[TraceSpan]
    clock_scope: str = "Cross-process wall-clock timestamps require synchronization."


class _Traffic(ObservationModel):
    started_at: float
    complete: bool = True
    at: float
    accepted: int
    rejected: int
    acks: int
    nacks: int
    errors: int


class _LatencyBucket(ObservationModel):
    second: int
    milliseconds: dict[int, int] = Field(default_factory=dict)


class _Checkpoint(ObservationModel):
    at: float = 0
    cursor: str = "0-0"
    brokers: dict[str, BrokerObservation] = Field(default_factory=dict)
    traffic: list[_Traffic] = Field(default_factory=list)
    latency: list[_LatencyBucket] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    alerts: list[OperationalAlert] = Field(default_factory=list)
    last_gap_at: float = 0


_SPAN_LIST = TypeAdapter(list[ObservedSpan])
_STRING = TypeAdapter(str)
_STRING_LIST = TypeAdapter(list[str])
_STRING_MAP = TypeAdapter(dict[str, str])
_OPTIONAL_STRING_LIST = TypeAdapter(list[str | None])
_OPTIONAL_FLOAT_LIST = TypeAdapter(list[float | None])
_STREAM = TypeAdapter(list[tuple[str, dict[str, str]]])


_APPEND_SPANS = """
for index, expected in ipairs({'stream', 'string'}) do
  local actual = redis.call('TYPE', KEYS[index]).ok
  if actual ~= 'none' and actual ~= expected then
    return redis.error_reply('Invalid span journal type')
  end
end
local maximum = tonumber(ARGV[3])
local saved = redis.call('GET', KEYS[2])
local count = saved and tonumber(saved) or 0
if (saved and not tonumber(saved)) or count < 0 or count > maximum
    or count ~= math.floor(count)
    or (not saved and redis.call('XLEN', KEYS[1]) ~= 0) then
  return redis.error_reply('Invalid span journal counter')
end
local total = count + tonumber(ARGV[2])
local removed = {}
local cursor = '-'
while total > maximum do
  local row = redis.call('XRANGE', KEYS[1], cursor, '+', 'COUNT', 1)[1]
  if not row then return redis.error_reply('Invalid span journal count') end
  local retained = nil
  for index = 1, #row[2], 2 do
    if row[2][index] == 'count' then retained = tonumber(row[2][index + 1]) end
  end
  if not retained or retained < 1 or retained > 2048
      or retained ~= math.floor(retained) then
    return redis.error_reply('Invalid span journal record')
  end
  total = total - retained
  table.insert(removed, row[1])
  cursor = '(' .. row[1]
end
redis.call('XADD', KEYS[1], '*', 'data', ARGV[1], 'count', ARGV[2])
for _, identity in ipairs(removed) do redis.call('XDEL', KEYS[1], identity) end
redis.call('SET', KEYS[2], total, 'EX', ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return total
"""


_PUBLISH = """
for index, expected in ipairs({'hash', 'zset', 'string'}) do
  local actual = redis.call('TYPE', KEYS[index]).ok
  if actual ~= 'none' and actual ~= expected then
    return redis.error_reply('Invalid observation registry type')
  end
end
local previous = redis.call('HGET', KEYS[1], ARGV[1])
local incoming = cjson.decode(ARGV[2])
if previous then
  local old = cjson.decode(previous)
  if old.instance_id == incoming.instance_id then
    if old.sequence >= incoming.sequence then return 0 end
  elseif old.started_at >= incoming.started_at then return 0 end
end
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[3])
for _, member in ipairs(expired) do
  redis.call('HDEL', KEYS[1], member)
  redis.call('ZREM', KEYS[2], member)
end
if not previous and redis.call('HLEN', KEYS[1]) >= tonumber(ARGV[5]) then
  redis.call('SET', KEYS[3], 'registry_capacity_exceeded', 'EX', ARGV[4])
  return -1
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZADD', KEYS[2], incoming.observed_at, ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return 1
"""
_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('PEXPIRE', KEYS[1], ARGV[2])
"""
_COMMIT = """
for index, expected in ipairs({'string', 'string', 'list', 'zset', 'hash', 'zset'}) do
  local actual = redis.call('TYPE', KEYS[index]).ok
  if actual ~= 'none' and actual ~= expected then
    return redis.error_reply('Invalid observation checkpoint type')
  end
end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local last = redis.call('LINDEX', KEYS[3], -1)
local second = math.floor(cjson.decode(ARGV[3]).finished_at)
local previousSecond = nil
if last then
  local valid, previous = pcall(cjson.decode, last)
  if not valid or type(previous) ~= 'table'
      or type(previous.finished_at) ~= 'number' then
    return redis.error_reply('Invalid retained observation')
  end
  previousSecond = math.floor(previous.finished_at)
end
local completed = cjson.decode(ARGV[6])
local traces = cjson.decode(ARGV[8])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[5])
if previousSecond == second then
  redis.call('LSET', KEYS[3], -1, ARGV[3])
else redis.call('RPUSH', KEYS[3], ARGV[3]) end
redis.call('LTRIM', KEYS[3], -tonumber(ARGV[4]), -1)
redis.call('EXPIRE', KEYS[3], ARGV[5])
for _, entry in ipairs(completed) do
  redis.call('ZADD', KEYS[4], entry[2], entry[1])
end
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', ARGV[7])
redis.call('EXPIRE', KEYS[4], ARGV[5])
for _, trace in ipairs(traces) do
  redis.call('HSET', KEYS[5], trace[1], trace[2])
  redis.call('ZADD', KEYS[6], trace[3], trace[1])
end
local expired = redis.call('ZRANGEBYSCORE', KEYS[6], '-inf', ARGV[9])
for _, trace in ipairs(expired) do
  redis.call('HDEL', KEYS[5], trace)
  redis.call('ZREM', KEYS[6], trace)
end
local excess = redis.call('ZCARD', KEYS[6]) - tonumber(ARGV[10])
if excess > 0 then
  local removed = redis.call('ZRANGE', KEYS[6], 0, excess - 1)
  for _, trace in ipairs(removed) do
    redis.call('HDEL', KEYS[5], trace)
    redis.call('ZREM', KEYS[6], trace)
  end
end
redis.call('EXPIRE', KEYS[5], ARGV[5])
redis.call('EXPIRE', KEYS[6], ARGV[5])
return 1
"""


class ObservabilityStore:
    """Persist bounded fleet observations, independent of browser polling.

    A fenced Redis lease elects one aggregator. Traffic comes from sequenced
    broker counters; latency uses exact trace parent identities across client
    and broker spans. P95 uses conservative one-millisecond histogram bins.
    Trace detail retains a deterministic sample plus failed traces. This store
    is operational telemetry, not an audit archive or a delivery dependency.
    """

    _prefix = "mas:observability:"
    _chunk_size = 2048

    def __init__(
        self, redis: Redis, settings: ObservationSettings | None = None
    ) -> None:
        self.redis = redis
        self.settings = settings or ObservationSettings()
        self._owner = uuid.uuid4().hex
        self._lock = asyncio.Lock()
        self._checkpoint: _Checkpoint | None = None
        self._spans: OrderedDict[str, CorrelationNode] = OrderedDict()
        self._pending: dict[str, CorrelationNode] = {}
        self._selected: dict[str, dict[str, ObservedSpan]] = {}

    async def publish_broker(self, observation: BrokerObservation) -> None:
        """Ignore obsolete sequences and incarnations atomically."""
        result = await self.redis.eval(
            _PUBLISH,
            3,
            self._prefix + "brokers",
            self._prefix + "members",
            self._prefix + "registry_overflow",
            observation.broker_id,
            observation.model_dump_json(),
            time.time() - self.settings.retention_seconds,
            self.settings.retention_seconds,
            self.settings.broker_limit,
        )
        if result == -1:
            raise ValueError("Fleet registry capacity exceeded; coverage is incomplete")

    async def ingest_spans(self, spans: list[ObservedSpan]) -> None:
        """Append delivery joins and sampled detail in bounded pipelined batches."""
        if len(spans) > 32_768:
            raise ValueError("An observation batch may contain at most 32768 spans")
        spans = [
            span
            for span in spans
            if self.settings.retains_span(
                span.name,
                int(span.trace_id, 16),
                is_reply=span.attributes.get("mas.is_reply") is True,
                failed=span.failed,
            )
        ]
        if not spans:
            return
        chunk_size = min(self._chunk_size, self.settings.max_pending_spans)
        chunks = [
            spans[offset : offset + chunk_size]
            for offset in range(0, len(spans), chunk_size)
        ]
        encoded = await asyncio.to_thread(
            lambda: [_SPAN_LIST.dump_json(chunk) for chunk in chunks]
        )
        pipeline = self.redis.pipeline(transaction=False)
        for chunk, data in zip(chunks, encoded, strict=True):
            pipeline.eval(
                _APPEND_SPANS,
                2,
                self._prefix + "spans",
                self._prefix + "span_count",
                data,
                len(chunk),
                self.settings.max_pending_spans,
                self.settings.retention_seconds,
            )
        await pipeline.execute()

    async def _members(self, now: float) -> list[FleetMember]:
        raw = _STRING_MAP.validate_python(
            await self.redis.hgetall(self._prefix + "brokers")
        )

        def prepare() -> list[FleetMember]:
            members: list[FleetMember] = []
            for value in raw.values():
                observation = BrokerObservation.model_validate_json(value)
                age = max(0, now - observation.observed_at)
                fresh = age <= self.settings.stale_after_seconds
                members.append(
                    FleetMember(
                        observation=observation,
                        fresh=fresh,
                        age_seconds=age,
                        status=observation.status if fresh else "stale",
                    )
                )
            return sorted(members, key=lambda member: member.observation.broker_id)

        return await asyncio.to_thread(prepare)

    async def _restore(self) -> _Checkpoint:
        raw = await self.redis.get(self._prefix + "checkpoint")
        checkpoint = (
            await asyncio.to_thread(
                _Checkpoint.model_validate_json, _STRING.validate_python(raw)
            )
            if raw is not None
            else _Checkpoint()
        )
        self._spans.clear()
        self._pending.clear()
        self._selected.clear()
        rows = _STREAM.validate_python(
            await self.redis.xrange(
                self._prefix + "spans",
                max=checkpoint.cursor,
            )
        )
        for _identity, fields in rows:
            for span in await asyncio.to_thread(
                _SPAN_LIST.validate_json, fields["data"]
            ):
                self._remember(span)
                if (
                    int(span.trace_id[-8:], 16) % self.settings.trace_sample_every == 0
                    or span.failed
                ):
                    selected = self._selected.setdefault(span.trace_id, {})
                    if len(selected) < 64:
                        selected[span.span_id] = span
        for identity in checkpoint.pending:
            span = self._spans.get(identity)
            if span is not None:
                self._pending[identity] = span
        return checkpoint

    def _remember(self, span: ObservedSpan) -> bool:
        identity = span.trace_id + ":" + span.span_id
        if identity in self._spans:
            return False
        message_id = span.attributes.get("mas.message_id")
        self._spans[identity] = CorrelationNode(
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            name=span.name,
            started_unix_ns=span.started_unix_ns,
            message_id=message_id
            if isinstance(message_id, str) and message_id
            else None,
        )
        while len(self._spans) > self.settings.max_pending_spans:
            self._spans.popitem(last=False)
        return True

    def _latency(self, endpoint: CorrelationNode) -> float | None:
        ancestor = endpoint
        visited: set[str] = set()
        for _ in range(64):
            if ancestor.span_id in visited:
                return None
            visited.add(ancestor.span_id)
            if ancestor.name in {
                "mas.agent.send",
                "mas.agent.request",
                "mas.agent.reply",
            }:
                latency = (endpoint.started_unix_ns - ancestor.started_unix_ns) / 1e6
                return latency if 0 <= latency <= 300_000 else None
            if ancestor.parent_span_id is None:
                return None
            parent = self._spans.get(ancestor.trace_id + ":" + ancestor.parent_span_id)
            if parent is None:
                return None
            ancestor = parent
        return None

    def _point(
        self, checkpoint: _Checkpoint, now: float, complete: bool
    ) -> PerformancePoint:
        traffic = [
            entry
            for entry in checkpoint.traffic
            if entry.at > now - self.settings.latency_window_seconds
        ]
        started = min((entry.started_at for entry in traffic), default=now)
        # The first baseline contributes no count; subsequent intervals use it.
        finished = checkpoint.at or now
        elapsed = max(0, finished - started)
        accepted = sum(entry.accepted for entry in traffic)
        histogram: Counter[int] = Counter()
        for bucket in checkpoint.latency:
            if bucket.second > now - self.settings.latency_window_seconds:
                histogram.update(bucket.milliseconds)
        samples = sum(histogram.values())
        p95: float | None = None
        if samples:
            remaining = math.ceil(samples * 0.95)
            for milliseconds, count in sorted(histogram.items()):
                remaining -= count
                if remaining <= 0:
                    p95 = float(milliseconds)
                    break
        counter_complete = complete and all(entry.complete for entry in traffic)
        coverage = (
            samples / accepted
            if accepted and counter_complete and samples <= accepted
            else None
        )
        return PerformancePoint(
            started_at=started,
            finished_at=finished,
            accepted_messages=accepted,
            rejected_messages=sum(entry.rejected for entry in traffic),
            accepted_rate=accepted / elapsed if elapsed > 0 else None,
            end_to_end_p95_ms=p95,
            latency_samples=samples,
            latency_coverage=coverage,
            delivery_acks=sum(entry.acks for entry in traffic),
            delivery_nacks=sum(entry.nacks for entry in traffic),
            redis_errors=sum(entry.errors for entry in traffic),
            complete=(
                counter_complete
                and checkpoint.last_gap_at < now - self.settings.latency_window_seconds
                and accepted > 0
                and samples == accepted
            ),
            counter_complete=counter_complete,
        )

    def _alerts(
        self,
        members: list[FleetMember],
        point: PerformancePoint,
        previous: list[OperationalAlert],
        now: float,
        gap: bool = False,
        registry_complete: bool = True,
    ) -> list[OperationalAlert]:
        conditions: dict[str, OperationalAlert] = {}
        if gap:
            conditions["coverage_gap:aggregator"] = OperationalAlert(
                alert_id="coverage_gap:aggregator",
                kind="coverage_gap",
                severity="warning",
                status="active",
                title="Delivery observation coverage gap",
                detail=(
                    "Missing logical message identity or bounded correlation "
                    "backlog overflow in the current window"
                ),
                opened_at=now,
                updated_at=now,
            )
        if not registry_complete:
            conditions["coverage_gap:fleet_registry"] = OperationalAlert(
                alert_id="coverage_gap:fleet_registry",
                kind="coverage_gap",
                severity="warning",
                status="active",
                title="Fleet registry capacity exceeded",
                detail=(
                    f"Registry limit: {self.settings.broker_limit}; "
                    "at least one broker heartbeat was rejected in retention"
                ),
                opened_at=now,
                updated_at=now,
            )
        for member in members:
            observation = member.observation
            identity = observation.broker_id
            kind: Literal["broker_stale", "broker_health"] | None = None
            if not member.fresh:
                kind = "broker_stale"
            elif observation.status != "healthy":
                kind = "broker_health"
            if kind:
                alert_id = kind + ":" + identity
                conditions[alert_id] = OperationalAlert(
                    alert_id=alert_id,
                    kind=kind,
                    severity="critical",
                    status="active",
                    title=f"Broker {identity} is {member.status}",
                    detail="; ".join(observation.issues)
                    or f"Last heartbeat age: {member.age_seconds:.1f}s",
                    opened_at=now,
                    updated_at=now,
                )
            for exporter in observation.exporters:
                if exporter.configured and exporter.status in {"degraded", "stale"}:
                    alert_id = "export_failure:" + identity + ":" + exporter.signal
                    conditions[alert_id] = OperationalAlert(
                        alert_id=alert_id,
                        kind="export_failure",
                        severity="warning",
                        status="active",
                        title=f"{identity}: {exporter.signal} export {exporter.status}",
                        detail=exporter.last_error or "No recent successful export",
                        opened_at=now,
                        updated_at=now,
                    )
            counters = observation.counters
            if counters.dropped_spans or not counters.scope_complete:
                alert_id = "coverage_gap:" + identity
                conditions[alert_id] = OperationalAlert(
                    alert_id=alert_id,
                    kind="coverage_gap",
                    severity="warning",
                    status="active",
                    title=f"{identity}: incomplete telemetry coverage",
                    detail=(
                        f"Dropped spans: {counters.dropped_spans}; "
                        f"dropped scoped updates: {counters.dropped_scope_updates}"
                    ),
                    opened_at=now,
                    updated_at=now,
                )
        if (
            point.end_to_end_p95_ms is not None
            and point.end_to_end_p95_ms >= self.settings.targets.end_to_end_p95_ms
        ):
            conditions["latency_slo"] = OperationalAlert(
                alert_id="latency_slo",
                kind="latency_slo",
                severity="warning",
                status="active",
                title="Delivery p95 exceeds target",
                detail=(
                    f"Measured {point.end_to_end_p95_ms:.0f}ms; "
                    f"target {self.settings.targets.end_to_end_p95_ms:.0f}ms"
                ),
                opened_at=now,
                updated_at=now,
            )
        # Capacity is not an idle-traffic requirement. Only alert with actual backlog.
        if (
            point.accepted_rate is not None
            and point.accepted_rate < self.settings.targets.accepted_rate
            and (point.accepted_messages + point.rejected_messages)
            / max(1, point.finished_at - point.started_at)
            >= self.settings.targets.accepted_rate
            and any(
                session.outbound > 0
                for member in members
                for session in member.observation.sessions
            )
            and point.finished_at - point.started_at
            >= self.settings.latency_window_seconds
        ):
            conditions["throughput_slo"] = OperationalAlert(
                alert_id="throughput_slo",
                kind="throughput_slo",
                severity="warning",
                status="active",
                title="Backlogged delivery below capacity target",
                detail=(
                    f"Accepted {point.accepted_rate:.1f}/s "
                    "while outbound messages remain queued"
                ),
                opened_at=now,
                updated_at=now,
            )
        result = dict(conditions)
        for old in previous:
            current = result.get(old.alert_id)
            if current is not None:
                result[old.alert_id] = current.model_copy(
                    update={"opened_at": old.opened_at}
                )
            elif old.status == "active" and self._recovered(
                old, members, point, gap, registry_complete
            ):
                result[old.alert_id] = old.model_copy(
                    update={
                        "status": "resolved",
                        "updated_at": now,
                        "resolved_at": now,
                    }
                )
            elif (
                old.status == "active"
                or old.updated_at >= now - self.settings.retention_seconds
            ):
                result[old.alert_id] = old
        return sorted(
            result.values(),
            key=lambda alert: (alert.status != "active", -alert.updated_at),
        )[:1000]

    def _recovered(
        self,
        alert: OperationalAlert,
        members: list[FleetMember],
        point: PerformancePoint,
        gap: bool,
        registry_complete: bool,
    ) -> bool:
        """Resolve a condition only from fresh evidence of recovery."""
        fresh = bool(members) and all(member.fresh for member in members)
        if alert.kind == "latency_slo":
            return fresh and point.complete and point.end_to_end_p95_ms is not None
        if alert.kind == "throughput_slo":
            return fresh and (
                (
                    point.counter_complete
                    and point.accepted_rate is not None
                    and point.accepted_rate >= self.settings.targets.accepted_rate
                    and point.finished_at - point.started_at
                    >= self.settings.latency_window_seconds
                )
                or not any(
                    session.outbound > 0
                    for member in members
                    for session in member.observation.sessions
                )
            )
        if alert.alert_id == "coverage_gap:aggregator":
            return fresh and not gap
        if alert.alert_id == "coverage_gap:fleet_registry":
            return fresh and registry_complete
        parts = alert.alert_id.split(":")
        member = next(
            (
                member
                for member in members
                if len(parts) > 1 and member.observation.broker_id == parts[1]
            ),
            None,
        )
        if member is None or not member.fresh:
            return False
        if alert.kind in {"broker_health", "broker_stale"}:
            return member.status == "healthy"
        if alert.kind == "export_failure":
            return any(
                exporter.signal == parts[-1]
                and exporter.status in {"healthy", "disabled"}
                for exporter in member.observation.exporters
            )
        counters = member.observation.counters
        return counters.scope_complete and counters.dropped_spans == 0

    async def flush(self) -> None:
        """Aggregate once across the fleet, with fenced atomic checkpoints."""
        async with self._lock:
            lease = self._prefix + "aggregator"
            owned = bool(await self.redis.eval(_RENEW, 1, lease, self._owner, 10_000))
            if not owned:
                owned = bool(
                    await self.redis.set(lease, self._owner, nx=True, px=10_000)
                )
                self._checkpoint = None
            if not owned:
                return
            checkpoint = self._checkpoint or await self._restore()
            # A failed or uncertain flush must rebuild from the committed cursor.
            self._checkpoint = None
            now = time.time()
            members = await self._members(now)
            registry_complete = (
                await self.redis.get(self._prefix + "registry_overflow") is None
            )
            accepted = rejected = acks = nacks = errors = 0
            complete = (
                registry_complete
                and bool(members)
                and all(member.fresh for member in members)
            )
            baselines: dict[str, BrokerObservation] = {}
            for member in members:
                observation = member.observation
                previous = checkpoint.brokers.get(observation.broker_id)
                if previous is None or previous.instance_id != observation.instance_id:
                    if previous is not None or any(
                        (
                            observation.counters.accepted_messages,
                            observation.counters.rejected_messages,
                            observation.counters.delivery_acks,
                            observation.counters.delivery_nacks,
                            observation.counters.redis_errors,
                        )
                    ):
                        complete = False
                elif observation.sequence > previous.sequence:
                    current = observation.counters
                    old = previous.counters
                    delta = (
                        current.accepted_messages - old.accepted_messages,
                        current.rejected_messages - old.rejected_messages,
                        current.delivery_acks - old.delivery_acks,
                        current.delivery_nacks - old.delivery_nacks,
                        current.redis_errors - old.redis_errors,
                    )
                    if min(delta) < 0:
                        complete = False
                    else:
                        accepted += delta[0]
                        rejected += delta[1]
                        acks += delta[2]
                        nacks += delta[3]
                        errors += delta[4]
                complete = complete and observation.counters.scope_complete
                baselines[observation.broker_id] = observation
            traffic = [
                entry
                for entry in checkpoint.traffic
                if entry.at >= now - self.settings.latency_window_seconds
            ]
            traffic.append(
                _Traffic(
                    started_at=checkpoint.at or now,
                    complete=complete or checkpoint.at == 0,
                    at=now,
                    accepted=accepted,
                    rejected=rejected,
                    acks=acks,
                    nacks=nacks,
                    errors=errors,
                )
            )
            rows = _STREAM.validate_python(
                await self.redis.xrange(
                    self._prefix + "spans",
                    min="(" + checkpoint.cursor,
                    count=64,
                )
            )
            cursor = checkpoint.cursor
            updated_traces: set[str] = set()
            gap_at = checkpoint.last_gap_at
            for row_cursor, fields in rows:
                cursor = row_cursor
                for span in await asyncio.to_thread(
                    _SPAN_LIST.validate_json, fields["data"]
                ):
                    if not self._remember(span):
                        continue
                    identity = span.trace_id + ":" + span.span_id
                    if span.name == "mas.agent.handle_message" or (
                        span.name == "mas.agent.transport.receive"
                        and span.attributes.get("mas.is_reply") is True
                    ):
                        message_id = span.attributes.get("mas.message_id")
                        if isinstance(message_id, str) and message_id:
                            self._pending[identity] = self._spans[identity]
                        else:
                            gap_at = now
                    if (
                        int(span.trace_id[-8:], 16) % self.settings.trace_sample_every
                        == 0
                        or span.failed
                    ):
                        selected = self._selected.setdefault(span.trace_id, {})
                        if len(selected) < 64:
                            selected[span.span_id] = span
                            updated_traces.add(span.trace_id)
            pending = {
                identity: span
                for identity, span in self._pending.items()
                if span.started_unix_ns / 1e9
                >= now - self.settings.latency_window_seconds
            }
            if len(pending) > self.settings.max_pending_spans:
                pending = dict(
                    list(pending.items())[-self.settings.max_pending_spans :]
                )
                complete = False
                gap_at = now
            identities = list(pending)
            message_keys = [
                pending[identity].message_id or identity for identity in identities
            ]
            processed = (
                _OPTIONAL_FLOAT_LIST.validate_python(
                    await self.redis.zmscore(self._prefix + "completed", message_keys)
                )
                if identities
                else []
            )
            latency = {
                bucket.second: Counter(bucket.milliseconds)
                for bucket in checkpoint.latency
                if bucket.second >= now - self.settings.latency_window_seconds
            }
            completed: list[tuple[str, float]] = []
            new_messages: set[str] = set()
            for identity, message_key, done in zip(
                identities, message_keys, processed, strict=True
            ):
                span = pending[identity]
                if done is not None or message_key in new_messages:
                    pending.pop(identity)
                    continue
                measured = self._latency(span)
                if measured is not None:
                    second = span.started_unix_ns // 1_000_000_000
                    latency.setdefault(second, Counter())[math.ceil(measured)] += 1
                    completed.append((message_key, span.started_unix_ns / 1e9))
                    new_messages.add(message_key)
                    pending.pop(identity)
            self._pending = pending
            checkpoint = _Checkpoint(
                at=now,
                cursor=cursor,
                brokers=baselines,
                traffic=traffic,
                latency=[
                    _LatencyBucket(second=second, milliseconds=dict(histogram))
                    for second, histogram in latency.items()
                ],
                pending=list(pending),
                alerts=checkpoint.alerts,
                last_gap_at=gap_at,
            )
            point = await asyncio.to_thread(self._point, checkpoint, now, complete)
            checkpoint = checkpoint.model_copy(
                update={
                    "alerts": self._alerts(
                        members,
                        point,
                        checkpoint.alerts,
                        now,
                        gap_at >= now - self.settings.latency_window_seconds,
                        registry_complete,
                    ),
                }
            )
            trace_ids = sorted(updated_traces)
            previous_traces: list[str | None] = []
            if updated_traces:
                previous_traces = _OPTIONAL_STRING_LIST.validate_python(
                    await self.redis.hmget(self._prefix + "traces", trace_ids)
                )
            selected_traces = {
                trace_id: dict(self._selected[trace_id]) for trace_id in trace_ids
            }

            def prepare_commit() -> tuple[str, str, str, str]:
                # Worker-owned copies cannot mutate the live cache after cancellation.
                trace_records: list[tuple[str, str, float]] = []
                for trace_id, previous_trace in zip(
                    trace_ids, previous_traces, strict=True
                ):
                    selected = selected_traces[trace_id]
                    if previous_trace is not None:
                        for span in _SPAN_LIST.validate_json(previous_trace):
                            if len(selected) < 64:
                                selected.setdefault(span.span_id, span)
                    spans = list(selected.values())
                    trace_records.append(
                        (
                            trace_id,
                            _SPAN_LIST.dump_json(spans).decode(),
                            max(span.finished_unix_ns for span in spans) / 1e9,
                        )
                    )
                return (
                    checkpoint.model_dump_json(),
                    point.model_dump_json(),
                    json.dumps(completed),
                    json.dumps(trace_records),
                )

            (
                checkpoint_json,
                point_json,
                completed_json,
                traces_json,
            ) = await asyncio.to_thread(prepare_commit)
            self._selected.update(selected_traces)
            committed = bool(
                await self.redis.eval(
                    _COMMIT,
                    6,
                    lease,
                    self._prefix + "checkpoint",
                    self._prefix + "history",
                    self._prefix + "completed",
                    self._prefix + "traces",
                    self._prefix + "trace_index",
                    self._owner,
                    checkpoint_json,
                    point_json,
                    self.settings.history_limit,
                    self.settings.retention_seconds,
                    completed_json,
                    now - self.settings.latency_window_seconds,
                    traces_json,
                    now - self.settings.retention_seconds,
                    self.settings.trace_limit,
                )
            )
            if not committed:
                self._checkpoint = None
                return
            self._checkpoint = checkpoint
            # Retain only bounded sampled trace state in this process.
            while len(self._selected) > self.settings.trace_limit:
                self._selected.pop(next(iter(self._selected)))

    async def fleet(self) -> FleetSnapshot:
        """Read current fleet health without becoming an aggregation dependency."""
        now = time.time()
        members = await self._members(now)
        registry_complete = (
            await self.redis.get(self._prefix + "registry_overflow") is None
        )
        raw = await self.redis.get(self._prefix + "checkpoint")
        checkpoint = (
            await asyncio.to_thread(
                _Checkpoint.model_validate_json, _STRING.validate_python(raw)
            )
            if raw is not None
            else _Checkpoint()
        )
        complete = (
            bool(members)
            and registry_complete
            and all(
                member.fresh and member.observation.counters.scope_complete
                for member in members
            )
            and checkpoint.at >= now - self.settings.stale_after_seconds
        )
        point = await asyncio.to_thread(self._point, checkpoint, now, complete)
        status: BrokerStatus = "unknown" if not members else "healthy"
        if members and any(member.status != "healthy" for member in members):
            status = "degraded"
        alerts = self._alerts(
            members,
            point,
            checkpoint.alerts,
            now,
            checkpoint.last_gap_at >= now - self.settings.latency_window_seconds,
            registry_complete,
        )
        if any(alert.status == "active" for alert in alerts):
            status = "degraded"
        return FleetSnapshot(
            generated_at=now,
            status=status,
            complete=complete,
            brokers=members,
            performance=point,
            targets=self.settings.targets,
            alerts=alerts,
            retention_seconds=self.settings.retention_seconds,
            trace_limit=self.settings.trace_limit,
            stale_after_seconds=self.settings.stale_after_seconds,
            trace_sample_every=self.settings.trace_sample_every,
            latency_window_seconds=self.settings.latency_window_seconds,
        )

    async def history(self, limit: int = 60) -> list[PerformancePoint]:
        """Return oldest-to-newest retained measurements."""
        if not 1 <= limit <= 1000:
            raise ValueError("History limit is outside configured retention")
        limit = min(limit, self.settings.history_limit)
        values = _STRING_LIST.validate_python(
            await self.redis.lrange(
                self._prefix + "history",
                -limit,
                -1,
            )
        )
        cutoff = time.time() - self.settings.retention_seconds
        return await asyncio.to_thread(
            lambda: [
                point
                for value in values
                if (point := PerformancePoint.model_validate_json(value)).finished_at
                >= cutoff
            ]
        )

    async def traces(self, limit: int = 30) -> list[TraceSummary]:
        """List the most recent retained trace sample."""
        if not 1 <= limit <= 1000:
            raise ValueError("Trace limit is outside configured retention")
        limit = min(limit, self.settings.trace_limit)
        identities = _STRING_LIST.validate_python(
            await self.redis.zrevrangebyscore(
                self._prefix + "trace_index",
                "+inf",
                time.time() - self.settings.retention_seconds,
                start=0,
                num=limit,
            )
        )
        if not identities:
            return []
        raw = _OPTIONAL_STRING_LIST.validate_python(
            await self.redis.hmget(self._prefix + "traces", identities)
        )
        return await asyncio.to_thread(
            lambda: [
                self._detail(_SPAN_LIST.validate_json(value)).summary
                for value in raw
                if value is not None
            ]
        )

    async def trace(self, trace_id: str) -> TraceDetail | None:
        """Prepare a safe, bounded span waterfall for a retained trace."""
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise ValueError("Invalid trace ID")
        raw = await self.redis.hget(self._prefix + "traces", trace_id)
        if raw is None:
            return None
        spans = await asyncio.to_thread(
            _SPAN_LIST.validate_json, _STRING.validate_python(raw)
        )
        if (
            max(span.finished_unix_ns for span in spans) / 1e9
            < time.time() - self.settings.retention_seconds
        ):
            return None
        return await asyncio.to_thread(self._detail, spans)

    @staticmethod
    def _detail(spans: list[ObservedSpan]) -> TraceDetail:
        ordered = sorted(spans, key=lambda span: (span.started_unix_ns, span.span_id))
        lookup = {span.span_id: span for span in ordered}
        started = min(span.started_unix_ns for span in ordered)
        finished = max(span.finished_unix_ns for span in ordered)
        prepared: list[TraceSpan] = []
        complete = len(spans) < 64
        skew = False
        latencies: list[float] = []
        for span in ordered:
            parent_id = span.parent_span_id
            visited = {span.span_id}
            depth = 0
            while parent_id is not None:
                parent = lookup.get(parent_id)
                if parent is None or parent_id in visited:
                    complete = False
                    break
                visited.add(parent_id)
                depth += 1
                if span.started_unix_ns < parent.started_unix_ns:
                    skew = True
                if (
                    span.name == "mas.agent.handle_message"
                    or (
                        span.name == "mas.agent.transport.receive"
                        and span.attributes.get("mas.is_reply") is True
                    )
                ) and parent.name in {
                    "mas.agent.send",
                    "mas.agent.request",
                    "mas.agent.reply",
                }:
                    value = (span.started_unix_ns - parent.started_unix_ns) / 1e6
                    if value >= 0:
                        latencies.append(value)
                parent_id = parent.parent_span_id
            prepared.append(
                TraceSpan(
                    span=span,
                    depth=depth,
                    offset_ms=(span.started_unix_ns - started) / 1e6,
                    duration_ms=(span.finished_unix_ns - span.started_unix_ns) / 1e6,
                )
            )
        return TraceDetail(
            summary=TraceSummary(
                trace_id=ordered[0].trace_id,
                started_at=started / 1e9,
                finished_at=finished / 1e9,
                span_count=len(spans),
                error_count=sum(span.failed for span in spans),
                message_ids=sorted(
                    {
                        value
                        for span in spans
                        if isinstance(
                            value := span.attributes.get("mas.message_id"), str
                        )
                    }
                ),
                services=sorted({span.service_name for span in spans}),
                end_to_end_ms=max(latencies) if latencies else None,
                complete=complete and not skew and bool(latencies),
                clock_skew_detected=skew,
            ),
            spans=prepared,
        )
