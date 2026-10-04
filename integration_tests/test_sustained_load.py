"""Fast real-infrastructure regression for the sustained acceptance harness."""

import os
from pathlib import Path

import pytest
from aiohttp import ClientSession
from mas_agent.config import TlsClientConfig
from mas_core.protocol import EnvelopeMessage, MessageMeta
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from integration_tests.load_support import (
    LoadConsumer,
    LoadMeasurements,
    OTLPReceiver,
    TraceSpanIdentity,
    run_sustained_load,
)


@pytest.mark.asyncio
async def test_shared_trace_requires_each_messages_exported_ingress_span() -> None:
    trace_id = "1234567890abcdef1234567890abcdef"
    parent_ids = ("1111111111111111", "2222222222222222")
    measurements = LoadMeasurements()
    consumer = LoadConsumer(
        measurements=measurements,
        server_addr="127.0.0.1:0",
        tls=TlsClientConfig("unused-ca", "unused-cert", "unused-key"),
    )
    for sequence, parent_id in enumerate(parent_ids):
        await consumer.on_message(
            EnvelopeMessage(
                message_id=f"message-{sequence}",
                sender_id="load_sender",
                target_id="load_worker",
                message_type="load.message",
                data={"sequence": sequence},
                meta=MessageMeta(traceparent=f"00-{trace_id}-{parent_id}-03"),
            )
        )
    assert measurements.message_parents == {
        f"message-{sequence}": TraceSpanIdentity(trace_id, parent_id)
        for sequence, parent_id in enumerate(parent_ids)
    }

    def export_ingress(parent_id: str) -> ExportTraceServiceRequest:
        export = ExportTraceServiceRequest()
        resource = export.resource_spans.add()
        service = resource.resource.attributes.add()
        service.key = "service.name"
        service.value.string_value = "broker"
        scope = resource.scope_spans.add()
        span = scope.spans.add()
        span.name = "mas.server.ingress.send"
        span.trace_id = bytes.fromhex(trace_id)
        span.span_id = bytes.fromhex(parent_id)
        message_type = span.attributes.add()
        message_type.key = "mas.message_type"
        message_type.value.string_value = "load.message"
        return export

    receiver = OTLPReceiver()
    await receiver.start()
    try:
        async with ClientSession() as client:
            for sequence, parent_id in enumerate(parent_ids):
                async with client.post(
                    f"{receiver.endpoint}/v1/traces",
                    data=export_ingress(parent_id).SerializeToString(),
                    headers={"Content-Type": "application/x-protobuf"},
                ) as response:
                    assert response.status == 200
                assert receiver.exported_ingress(
                    measurements.message_parents, services={"broker"}
                ) == {f"message-{index}" for index in range(sequence + 1)}
        assert (
            receiver.exported_ingress(
                measurements.message_parents, services={"other-broker"}
            )
            == set()
        )
    finally:
        await receiver.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duration", "latency_limit_ms"),
    [(float("nan"), 100), (float("inf"), 100), (2, float("nan")), (2, float("inf"))],
)
async def test_sustained_load_rejects_nonfinite_inputs_before_starting(
    tmp_path: Path, duration: float, latency_limit_ms: float
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        await run_sustained_load(
            tmp_path, duration=duration, latency_limit_ms=latency_limit_ms
        )
    assert not (tmp_path / "redis").exists()


@pytest.mark.asyncio
async def test_sustained_load_reports_latency_failure_with_protocol_and_export_proof(
    tmp_path: Path,
) -> None:
    report = await run_sustained_load(
        tmp_path,
        duration=2,
        target_rate=100,
        latency_limit_ms=0.001,
        broker_mode="in_process",
    )
    assert not report.passed
    assert not report.gates["latency"]
    assert all(
        report.gates[gate]
        for gate in (
            "all_planned_accepted",
            "no_rpc_errors",
            "no_accepted_loss",
            "rbac_enforced",
            "tracing_propagated",
            "tracing_exported",
            "broker_tracing_exported",
        )
    ), report.gates
    assert report.end_to_end_p95_ms is not None
    assert report.end_to_end_p95_ms > 0.001
    assert report.latency_limit_ms == 0.001
    assert report.accepted_messages == 204
    assert report.received_messages == 204
    assert report.exported_handler_messages == 204
    assert report.rbac_denied_probe and not report.acl_shortcut_present
    assert report.wait_for_aof and report.required_replica_confirmations == 1
    assert report.actual_redis_replica_processes == 2
    assert report.acceptance_interval_seconds > 0
    assert report.process_cpu_seconds >= report.event_loop_thread_cpu_seconds >= 0
    assert report.process_cpu_percent >= report.event_loop_thread_cpu_percent >= 0
    assert report.event_loop_lag_count >= report.event_loop_lag_samples_retained > 0
    assert report.event_loop_lag_max_ms is not None
    assert report.event_loop_lag_p95_ms is not None
    assert report.event_loop_lag_max_ms >= report.event_loop_lag_p95_ms >= 0
    assert report.broker_mode == "in_process"
    assert report.cpu_measurement_scope == "load_driver_and_brokers"


@pytest.mark.asyncio
async def test_sustained_load_uses_distinct_broker_processes_and_flushes_their_traces(
    tmp_path: Path,
) -> None:
    report = await run_sustained_load(tmp_path, duration=0.1, target_rate=20)
    assert report.broker_mode == "process"
    assert report.cpu_measurement_scope == "load_driver"
    assert len(set(report.broker_process_ids)) == 2
    assert os.getpid() not in report.broker_process_ids
    assert report.broker_exit_codes == [0, 0]
    assert report.accepted_messages == report.received_messages == 3
    assert report.latency_limit_ms == 300
    assert report.exported_handler_messages == report.exported_broker_messages == 3
    assert report.gates["broker_isolation"] and report.gates["broker_shutdown"]
    assert report.gates["broker_tracing_exported"]
    assert report.gates["tracing_exported"] and report.gates["rbac_enforced"]
    assert not report.errors and report.lost_accepted_messages == 0
    assert report.delivery_latency["outbound_to_handler"].count == 3
    assert report.delivery_latency["transport_write"].count == 3
    assert report.delivery_latency["receive_to_handler"].count == 3
    assert report.delivery_trace_coverage.accepted_messages == 3
    assert report.delivery_trace_coverage.fully_observed_messages == 3
    assert report.delivery_trace_coverage.incomplete_messages == 0
    assert (
        sum(consumer.deliveries for consumer in report.delivery_consumers.values()) == 3
    )
