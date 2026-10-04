"""Export authoritative dashboard response schemas and serialized test fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_core.observability import (
    BrokerCounters,
    BrokerObservation,
    BrokerSession,
    ExportHealth,
    FleetMember,
    FleetSnapshot,
    ObservedSpan,
    PerformancePoint,
    SloTargets,
    TraceDetail,
    TraceSpan,
    TraceSummary,
)
from mas_core.protocol import JsonObject, JsonValue, validate_json_object
from mas_core.telemetry.runtime import TelemetrySnapshot
from mas_gateway.circuit_breaker import CircuitStatus
from mas_server.management import (
    ActivitySummary,
    AgentSummary,
    HealthReport,
    ManagementSnapshot,
    QueueSummary,
    SessionSummary,
)
from pydantic import BaseModel, ConfigDict


class DashboardContracts(BaseModel):
    """The management reader responses, serialized without omitted defaults."""

    model_config = ConfigDict(extra="forbid")

    snapshot: ManagementSnapshot
    history: list[PerformancePoint]
    traces: list[TraceSummary]
    trace: TraceDetail


def _require_output_properties(value: JsonValue) -> None:
    """Make serialized model keys required and reject undeclared model fields."""
    if isinstance(value, dict):
        for child in value.values():
            _require_output_properties(child)
        properties = value.get("properties")
        if isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False
            for property_schema in properties.values():
                if isinstance(property_schema, dict):
                    property_schema.pop("title", None)
    elif isinstance(value, list):
        for child in value:
            _require_output_properties(child)


def response_schema() -> JsonObject:
    """Derive types and validators from the actual serialized Python DTOs."""
    schema = validate_json_object(
        DashboardContracts.model_json_schema(mode="serialization")
    )
    schema["$id"] = "https://mas.local/dashboard/contracts"
    _require_output_properties(schema)
    return schema


def response_fixtures() -> DashboardContracts:
    """Produce realistic examples through the same serializer as the readers."""
    point = PerformancePoint(
        started_at=1_790_000_000,
        finished_at=1_790_000_001,
        accepted_messages=1000,
        accepted_rate=1000,
        end_to_end_p95_ms=270,
        latency_samples=1000,
        latency_coverage=1,
        complete=True,
        counter_complete=True,
    )
    exporter = ExportHealth(
        signal="traces",
        configured=True,
        attempts=4,
        successes=4,
        exported_items=1000,
        last_attempt_at=point.finished_at,
        last_success_at=point.finished_at,
        age_seconds=0,
        status="healthy",
    )
    broker = BrokerObservation(
        broker_id="broker-1",
        instance_id="incarnation-1",
        sequence=2,
        observed_at=point.finished_at,
        started_at=point.started_at - 10,
        status="healthy",
        grpc_address="127.0.0.1:50051",
        management_url="http://127.0.0.1:8080",
        redis_available=True,
        redis_latency_ms=0.2,
        sessions=[
            BrokerSession(
                agent_id="worker",
                instance_id="worker-1",
                inflight=2,
                outbound=1,
                worker_running=True,
            )
        ],
        counters=BrokerCounters(accepted_messages=1000, delivery_acks=998),
        exporters=[exporter],
    )
    fleet = FleetSnapshot(
        generated_at=point.finished_at,
        status="healthy",
        complete=True,
        brokers=[
            FleetMember(observation=broker, fresh=True, age_seconds=0, status="healthy")
        ],
        performance=point,
        targets=SloTargets(),
        alerts=[],
        retention_seconds=3600,
        trace_limit=10_000,
        stale_after_seconds=5,
        trace_sample_every=100,
        latency_window_seconds=60,
    )
    summary = TraceSummary(
        trace_id="a" * 32,
        started_at=point.started_at,
        finished_at=point.started_at + 0.27,
        span_count=1,
        error_count=0,
        message_ids=["message-1"],
        services=["mas-agent"],
        end_to_end_ms=270,
        complete=True,
        clock_skew_detected=False,
    )
    span = ObservedSpan(
        trace_id=summary.trace_id,
        span_id="b" * 16,
        name="mas.agent.handle_message",
        service_name="mas-agent",
        started_unix_ns=int(point.started_at * 1_000_000_000),
        finished_unix_ns=int((point.started_at + 0.27) * 1_000_000_000),
        attributes={"mas.message_id": "message-1", "mas.agent_id": "worker"},
    )
    return DashboardContracts(
        snapshot=ManagementSnapshot(
            generated_at=point.finished_at,
            uptime_seconds=11,
            health=HealthReport(
                status="healthy", redis_available=True, redis_latency_ms=0.2
            ),
            agents=[
                AgentSummary(
                    agent_id="worker",
                    capabilities=["compute"],
                    status="active",
                    sessions=[
                        SessionSummary(
                            instance_id="worker-1",
                            inflight=2,
                            outbound=1,
                            worker_running=True,
                        )
                    ],
                )
            ],
            queues=[QueueSummary(stream="agent.stream:worker", pending=2, waiting=1)],
            queues_complete=True,
            backlog=3,
            dead_letters=0,
            recent_activity=[
                ActivitySummary(
                    message_id="message-1",
                    timestamp=point.started_at,
                    sender_id="producer",
                    target_id="worker",
                    message_type="task",
                    decision="ALLOWED",
                    latency_ms=2,
                    correlation_id=None,
                    violations=[],
                )
            ],
            telemetry=TelemetrySnapshot(
                ingress={"send:ALLOWED": 1000},
                delivery_acks=998,
                delivery_nacks=0,
                retryable_nacks=0,
                dead_letter_writes=0,
                dead_letter_errors=0,
                redis_errors=0,
                active_sessions=1,
                policy_samples=1000,
                policy_latency_mean_ms=2,
                policy_latency_max_ms=5,
                export_enabled=True,
            ),
            features={"rbac": True, "audit": True, "tracing": True},
            circuits={"worker": CircuitStatus()},
            broker_id=broker.broker_id,
            fleet=fleet,
            exporters=[exporter],
        ),
        history=[point],
        traces=[summary],
        trace=TraceDetail(
            summary=summary,
            spans=[TraceSpan(span=span, depth=0, offset_ms=0, duration_ms=270)],
        ),
    )


def main() -> None:
    """Write artifacts, or check they match the current backend contracts."""
    arguments = sys.argv[1:]
    if arguments not in ([], ["--check"]):
        raise SystemExit("Usage: uv run python tools/dashboard_contracts.py [--check]")
    root = Path(__file__).resolve().parent.parent
    artifacts = {
        root / "dashboard/src/lib/contracts.schema.json": json.dumps(
            response_schema(), indent=2, sort_keys=True, allow_nan=False
        )
        + "\n",
        root / "dashboard/tests/fixtures.json": response_fixtures().model_dump_json(
            indent=2
        )
        + "\n",
    }
    for path, content in artifacts.items():
        if arguments:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Stale dashboard contract: {path.relative_to(root)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
