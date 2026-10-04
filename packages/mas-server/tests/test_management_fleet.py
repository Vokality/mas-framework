"""Fleet reader and independently authorized OTLP boundary regressions."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from mas_core.observability import TraceDetail, TraceSummary
from mas_core.telemetry.runtime import TelemetryRuntime
from mas_gateway import audit as audit_module
from mas_gateway.audit import AuditModule
from mas_gateway.config import GatewaySettings
from mas_server.management import (
    ManagementService,
    ManagementSettings,
    ManagementTlsSettings,
    TelemetryIngestSettings,
)
from mas_server.management_auth import OidcSettings
from mas_server.observation import decode_otlp_spans
from mas_server.sessions import SessionManager
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

_TRACE_ID = f"{100:032x}"


def _service(
    redis: Redis, *, payload_limit: int = 4096, span_limit: int = 20
) -> ManagementService:
    return ManagementService(
        settings=ManagementSettings(
            port=0,
            auth_mode="token",
            token="reader",
            telemetry_ingest=TelemetryIngestSettings(
                token="writer",
                max_payload_bytes=payload_limit,
                max_spans_per_request=span_limit,
            ),
        ),
        redis=redis,
        sessions=SessionManager(agents={}, redis=redis),
        agents={},
        gateway=GatewaySettings(),
        audit=AuditModule(redis, file_sink=None),
        circuit_breaker=None,
        is_running=lambda: True,
        broker_id="broker",
    )


def _export(*, spans: int = 1) -> bytes:
    export = ExportTraceServiceRequest()
    resource = export.resource_spans.add()
    service = resource.resource.attributes.add()
    service.key, service.value.string_value = "service.name", "agent-worker"
    scope = resource.scope_spans.add()
    for index in range(spans):
        span = scope.spans.add()
        span.name = "mas.agent.handle_message"
        span.trace_id = bytes.fromhex(_TRACE_ID)
        span.span_id = (index + 1).to_bytes(8, "big")
        span.end_time_unix_nano = time.time_ns()
        span.start_time_unix_nano = span.end_time_unix_nano - 10_000_000
        for key, value in {
            "mas.message_id": "message",
            "mas.data_json": "business-secret",
            "authorization": "credential-secret",
        }.items():
            attribute = span.attributes.add()
            attribute.key, attribute.value.string_value = key, value
        span.status.message = "exception-secret"
        event = span.events.add()
        event.name = "event-secret"
    return export.SerializeToString()


def test_ingest_configuration_requires_independent_explicit_write_access() -> None:
    with pytest.raises(ValueError, match="separate"):
        ManagementSettings(
            auth_mode="token",
            token="same",
            telemetry_ingest=TelemetryIngestSettings(token="same"),
        )
    with pytest.raises(ValueError, match="write"):
        TelemetryIngestSettings(
            auth_mode="oidc",
            oidc=OidcSettings(
                issuer="http://localhost",
                audience="mas",
                jwks_url="http://localhost/jwks",
            ),
        )
    with pytest.raises(ValueError, match="loopback"):
        ManagementSettings(
            host="0.0.0.0",
            auth_mode="oidc",
            oidc=OidcSettings(
                issuer="http://localhost",
                audience="mas",
                jwks_url="http://localhost/jwks",
            ),
            tls=ManagementTlsSettings("cert", "key"),
            telemetry_ingest=TelemetryIngestSettings(auth_mode="local"),
        )


def test_otlp_filters_business_values_events_and_exception_details() -> None:
    spans = decode_otlp_spans(_export(), limit=10)
    assert len(spans) == 1
    assert spans[0].attributes == {"mas.message_id": "message"}
    assert spans[0].service_name == "mas-agent"
    text = spans[0].model_dump_json()
    assert "secret" not in text
    with pytest.raises(ValueError, match="invalid_protobuf"):
        decode_otlp_spans(b"\x80", limit=10)
    with pytest.raises(ValueError, match="too_many_spans"):
        decode_otlp_spans(_export(spans=2), limit=1)
    arbitrary = ExportTraceServiceRequest()
    arbitrary.ParseFromString(_export())
    arbitrary.resource_spans[0].scope_spans[0].spans[0].name = "mas.business.secret"
    assert decode_otlp_spans(arbitrary.SerializeToString(), limit=10) == []


async def test_reader_grants_cannot_ingest_and_writer_cannot_read(redis: Redis) -> None:
    service = _service(redis)
    await service.start()
    try:
        async with ClientSession() as client:
            for route in ("/api/history", "/api/traces", "/api/traces/" + "a" * 32):
                async with client.get(
                    service.url + route, headers={"Authorization": "Bearer writer"}
                ) as response:
                    assert response.status == 401
            async with client.post(
                service.url + "/v1/traces",
                data=_export(),
                headers={
                    "Authorization": "Bearer reader",
                    "Content-Type": "application/x-protobuf",
                },
            ) as response:
                assert response.status == 401
            async with client.post(
                service.url + "/v1/traces",
                data=_export(),
                headers={
                    "Authorization": "Bearer writer",
                    "Content-Type": "application/x-protobuf",
                },
            ) as response:
                assert response.status == 200
                assert response.content_type == "application/x-protobuf"
            await service._observations.flush()
            async with client.get(
                service.url + "/api/traces", headers={"Authorization": "Bearer reader"}
            ) as response:
                assert response.status == 200
                summaries = TypeAdapter(list[TraceSummary]).validate_json(
                    await response.read()
                )
                assert len(summaries) == 1 and summaries[0].trace_id == _TRACE_ID
            async with client.get(
                service.url + "/api/traces/" + _TRACE_ID,
                headers={"Authorization": "Bearer reader"},
            ) as response:
                assert response.status == 200
                detail = TraceDetail.model_validate_json(await response.read())
                assert detail.spans[0].offset_ms == 0
                assert "secret" not in detail.model_dump_json()
    finally:
        await service.stop()


async def test_collector_authorization_is_audited_without_export_feedback(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intake audits stay durable without creating the next exported intake."""
    tracer = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    meter = MeterProvider()
    telemetry = TelemetryRuntime(
        enabled=True,
        tracer=tracer.get_tracer("collector-regression"),
        tracer_provider=tracer,
        meter_provider=meter,
    )
    monkeypatch.setattr(audit_module, "get_telemetry", lambda: telemetry)
    service = _service(redis)
    await service.start()
    try:
        async with ClientSession() as client:
            for token, status in (("reader", 401), ("writer", 200)):
                async with client.post(
                    service.url + "/v1/traces",
                    data=_export(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/x-protobuf",
                    },
                ) as response:
                    assert response.status == status
        assert await redis.xlen("audit:security_events") == 2
        rows = TypeAdapter(list[tuple[str, dict[str, str]]]).validate_python(
            await redis.xrange("audit:security_events")
        )
        assert [fields["event_type"] for _identity, fields in rows] == [
            "TELEMETRY_INGEST_DENIED",
            "TELEMETRY_INGEST_ALLOWED",
        ]
        assert await telemetry.drain_spans() == []
        assert exporter.get_finished_spans() == ()
        await service._audit.log_security_event("ordinary_authorization", {})
        assert {span.name for span in await telemetry.drain_spans()} == {
            "mas.gateway.audit.commit_batch",
            "mas.gateway.audit.append_batch",
            "mas.gateway.audit.confirm_batch",
        }
        await asyncio.gather(
            service._audit.log_security_event(
                "collector_authorization", {}, instrumented=False
            ),
            service._audit.log_security_event("ordinary_authorization", {}),
        )
        assert {span.name for span in await telemetry.drain_spans()} == {
            "mas.gateway.audit.commit_batch",
            "mas.gateway.audit.append_batch",
            "mas.gateway.audit.confirm_batch",
        }
        assert await redis.xlen("audit:security_events") == 5
        assert len(exporter.get_finished_spans()) == 6
    finally:
        await service.stop()
        await service._audit.close()
        await telemetry.shutdown()


@pytest.mark.parametrize(
    "route",
    [
        "/api/history?limit=0",
        "/api/history?limit=501",
        "/api/traces?unknown=1",
        "/api/traces?limit=2&limit=3",
        "/api/traces/not-a-trace",
        "/api/traces/" + "0" * 32,
    ],
)
async def test_reader_query_bounds_are_validated(redis: Redis, route: str) -> None:
    service = _service(redis)
    await service.start()
    try:
        async with (
            ClientSession() as client,
            client.get(
                service.url + route, headers={"Authorization": "Bearer reader"}
            ) as response,
        ):
            assert response.status == 400
    finally:
        await service.stop()


async def test_collector_rejects_malformed_and_oversized_batches(redis: Redis) -> None:
    service = _service(redis, payload_limit=512, span_limit=1)
    await service.start()
    try:
        async with ClientSession() as client:
            for body, status in (
                (b"\x80", 400),
                (_export(spans=2), 413),
                (b"x" * 513, 413),
            ):
                async with client.post(
                    service.url + "/v1/traces",
                    data=body,
                    headers={
                        "Authorization": "Bearer writer",
                        "Content-Type": "application/x-protobuf",
                    },
                ) as response:
                    assert response.status == status
            async with client.post(
                service.url + "/v1/traces",
                data=_export(),
                headers={
                    "Authorization": "Bearer writer",
                    "Content-Type": "application/json",
                },
            ) as response:
                assert response.status == 415
    finally:
        await service.stop()


async def test_retained_reader_storage_error_is_sanitized(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(redis)
    monkeypatch.setattr(
        service._observations,
        "history",
        AsyncMock(side_effect=ConnectionError("storage-secret")),
    )
    await service.start()
    try:
        async with (
            ClientSession() as client,
            client.get(
                service.url + "/api/history", headers={"Authorization": "Bearer reader"}
            ) as response,
        ):
            assert response.status == 503
            assert await response.text() == "observations_unavailable"
    finally:
        await service.stop()
