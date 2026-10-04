"""Actual OTLP boundary regressions for paired delivery timing evidence."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from integration_tests.load_support import OTLPReceiver


def _export(
    name: str, started_ms: int, finished_ms: int, attributes: dict[str, str | int]
) -> ExportTraceServiceRequest:
    export = ExportTraceServiceRequest()
    span = export.resource_spans.add().scope_spans.add().spans.add()
    span.name = name
    span.start_time_unix_nano = started_ms * 1_000_000
    span.end_time_unix_nano = finished_ms * 1_000_000
    for key, value in attributes.items():
        attribute = span.attributes.add()
        attribute.key = key
        if isinstance(value, str):
            attribute.value.string_value = value
        else:
            attribute.value.int_value = value
    return export


@pytest.mark.asyncio
async def test_delivery_timings_join_out_of_order_exports_and_overlap_write() -> None:
    receiver = OTLPReceiver()
    identity: dict[str, str | int] = {
        "mas.delivery_id": "delivery",
        "mas.message_id": "message",
    }
    exports = (
        _export("mas.agent.transport.receive", 1100, 1101, identity),
        _export(
            "mas.server.delivery.deliver_entry",
            1001,
            1002,
            {
                **identity,
                "mas.redis.entry_timestamp_ms": 1000,
                "mas.delivery.queued_at_unix_ns": 1002 * 1_000_000,
            },
        ),
        _export(
            "mas.server.transport.write",
            1050,
            1200,
            {
                **identity,
                "mas.instance_id": "worker-instance",
                "mas.outbound_queue_size": 2,
                "mas.inflight_count": 5,
            },
        ),
        _export("mas.agent.handle_message", 1103, 1104, identity),
    )
    await receiver.start()
    try:
        async with ClientSession() as client:
            for export in exports:
                async with client.post(
                    f"{receiver.endpoint}/v1/traces",
                    data=export.SerializeToString(),
                    headers={"Content-Type": "application/x-protobuf"},
                ) as response:
                    assert response.status == 200
        assert receiver.delivery_latency({"other-message"}) == {}
        latency = receiver.delivery_latency({"message"})
        assert latency["stream_to_outbound"].p95_ms == 2
        assert latency["outbound_queue"].p95_ms == 48
        assert latency["transport_write"].p95_ms == 150
        assert latency["write_to_receive"].p95_ms == 50
        assert latency["receive_to_handler"].p95_ms == 2
        assert latency["outbound_to_handler"].p95_ms == 101
        assert all(stage.count == 1 for stage in latency.values())
        coverage = receiver.delivery_coverage({"message", "missing-message"})
        assert coverage.accepted_messages == 2
        assert coverage.fully_observed_messages == 1
        assert coverage.incomplete_messages == 1
        backlog = receiver.consumer_backlogs({"message"})["worker-instance"]
        assert backlog.deliveries == 1
        assert backlog.outbound_queue_max == backlog.outbound_queue_p95 == 2
        assert backlog.inflight_max == backlog.inflight_p95 == 5
    finally:
        await receiver.stop()
