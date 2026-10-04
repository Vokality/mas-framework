"""Generate populated browser examples through real management DTO serializers."""

from __future__ import annotations

import sys
from pathlib import Path

from mas_core.observability import (
    FleetMember,
    ObservedSpan,
    OperationalAlert,
    PerformancePoint,
    TraceDetail,
    TraceSpan,
    TraceSummary,
)
from mas_gateway.circuit_breaker import CircuitStatus
from mas_server.management import ManagementSnapshot
from pydantic import BaseModel, ConfigDict

from tools.dashboard_contracts import response_fixtures

HOSTILE_TEXT = "<img src=x onerror=globalThis.__xss=1>"


class BrowserExamples(BaseModel):
    """Typed populated and empty response datasets used by actual Chromium tests."""

    model_config = ConfigDict(extra="forbid")
    snapshot: ManagementSnapshot
    empty_snapshot: ManagementSnapshot
    history: list[PerformancePoint]
    traces: list[TraceSummary]
    trace: TraceDetail


def browser_examples() -> BrowserExamples:
    """Keep dataset size sufficient to exercise filtering and multiple pages."""
    base = response_fixtures()
    snapshot = base.snapshot
    fleet = snapshot.fleet
    if fleet is None:
        raise ValueError("Browser examples require the populated fleet DTO")
    agents = [
        snapshot.agents[0].model_copy(
            update={
                "agent_id": f"agent-{index:02}",
                "status": "inactive" if index % 3 == 0 else "active",
                "capabilities": ["compute", HOSTILE_TEXT]
                if index == 1
                else ["compute"],
                "sessions": [] if index % 3 == 0 else snapshot.agents[0].sessions,
            }
        )
        for index in range(1, 31)
    ]
    brokers = [
        FleetMember(
            observation=fleet.brokers[0].observation.model_copy(
                update={
                    "broker_id": f"broker-{index:02}",
                    "exporters": [
                        fleet.brokers[0].observation.exporters[0],
                        fleet.brokers[0]
                        .observation.exporters[0]
                        .model_copy(
                            update={
                                "signal": "metrics",
                                "status": "degraded" if index % 3 == 0 else "healthy",
                                "failures": int(index % 3 == 0),
                                "last_error": "export_rejected"
                                if index % 3 == 0
                                else None,
                            }
                        ),
                    ],
                }
            ),
            fresh=index % 3 != 0,
            age_seconds=10 if index % 3 == 0 else 0,
            status="stale" if index % 3 == 0 else "healthy",
        )
        for index in range(1, 31)
    ]
    alerts = [
        OperationalAlert(
            alert_id=f"alert-{index:02}",
            kind="broker_stale" if index % 2 else "latency_slo",
            severity="critical" if index % 3 == 0 else "warning",
            status="resolved" if index % 2 else "active",
            title=HOSTILE_TEXT if index == 2 else f"Condition {index:02}",
            detail=f"Recorded operational condition {index:02}",
            opened_at=fleet.generated_at - index,
            updated_at=fleet.generated_at,
            resolved_at=fleet.generated_at if index % 2 else None,
        )
        for index in range(1, 31)
    ]
    history = [
        base.history[0].model_copy(
            update={
                "started_at": fleet.generated_at - 180 + index,
                "finished_at": fleet.generated_at - 179 + index,
                "accepted_rate": 1000 + index,
                "end_to_end_p95_ms": 200 + index,
                "complete": index % 2 == 0,
            }
        )
        for index in range(180)
    ]
    traces = [
        base.trace.summary.model_copy(
            update={
                "trace_id": f"{index:032x}",
                "message_ids": [f"message-{index:02}"],
                "started_at": fleet.generated_at - 100 + index,
                "finished_at": fleet.generated_at - 100 + index + index / 100,
                "end_to_end_ms": index * 10,
                "error_count": int(index % 3 == 0),
                "span_count": 3,
                "complete": index % 2 == 0,
            }
        )
        for index in range(1, 46)
    ]
    detail_summary = traces[-1]
    spans = [
        ObservedSpan(
            trace_id=detail_summary.trace_id,
            span_id=f"{index:016x}",
            parent_span_id=f"{index - 1:016x}" if index > 1 else None,
            name=("mas.agent.send", "mas.rpc.send", "mas.agent.handle_message")[
                index - 1
            ],
            service_name="mas-agent" if index != 2 else "mas-server",
            started_unix_ns=int((detail_summary.started_at + (index - 1) / 10) * 1e9),
            finished_unix_ns=int((detail_summary.started_at + index / 10) * 1e9),
            failed=index == 3,
            attributes={"mas.message_id": HOSTILE_TEXT, "mas.agent_id": "agent-01"},
        )
        for index in range(1, 4)
    ]
    detail = TraceDetail(
        summary=detail_summary,
        spans=[
            TraceSpan(span=span, depth=index, offset_ms=index * 100, duration_ms=100)
            for index, span in enumerate(spans)
        ],
    )
    snapshot = snapshot.model_copy(
        update={
            "agents": agents,
            "queues": [
                snapshot.queues[0].model_copy(
                    update={
                        "stream": f"agent.stream:agent-{index:02}",
                        "pending": index,
                        "waiting": 30 - index,
                    }
                )
                for index in range(1, 31)
            ]
            if snapshot.queues
            else [],
            "recent_activity": [
                snapshot.recent_activity[0].model_copy(
                    update={
                        "message_id": HOSTILE_TEXT
                        if index == 1
                        else f"activity-{index:02}",
                        "target_id": f"agent-{index:02}",
                        "timestamp": fleet.generated_at - index,
                        "decision": "AUTHZ_DENIED" if index % 3 == 0 else "ALLOWED",
                        "latency_ms": index,
                    }
                )
                for index in range(1, 31)
            ]
            if snapshot.recent_activity
            else [],
            "fleet": fleet.model_copy(update={"brokers": brokers, "alerts": alerts}),
            "circuits": {agent.agent_id: CircuitStatus() for agent in agents},
        }
    )
    empty = snapshot.model_copy(
        update={
            "agents": [],
            "queues": [],
            "recent_activity": [],
            "circuits": {},
            "backlog": 0,
            "fleet": fleet.model_copy(
                update={
                    "brokers": [],
                    "alerts": [],
                    "performance": PerformancePoint(
                        started_at=fleet.generated_at,
                        finished_at=fleet.generated_at,
                    ),
                }
            ),
        }
    )
    return BrowserExamples.model_validate(
        {
            "snapshot": snapshot.model_dump(mode="json"),
            "empty_snapshot": empty.model_dump(mode="json"),
            "history": [point.model_dump(mode="json") for point in history],
            "traces": [summary.model_dump(mode="json") for summary in traces],
            "trace": detail.model_dump(mode="json"),
        }
    )


def main() -> None:
    """Emit deterministic DTO examples or verify the checked-in fixture."""
    arguments = sys.argv[1:]
    if arguments not in ([], ["--check"]):
        raise SystemExit(
            "Usage: uv run python -m "
            "dashboard.tests.generate_browser_fixtures [--check]"
        )
    destination = Path(__file__).with_name("browser-fixtures.json")
    content = browser_examples().model_dump_json(indent=2) + "\n"
    if arguments:
        if destination.read_text() != content:
            raise SystemExit("Stale browser fixtures")
    else:
        destination.write_text(content)


if __name__ == "__main__":
    main()
