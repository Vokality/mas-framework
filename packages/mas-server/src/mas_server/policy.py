"""Security policy pipeline for message ingress."""

from __future__ import annotations

import time

from mas_core import EnvelopeMessage, SpanKind, get_telemetry
from mas_gateway import (
    ActionPolicy,
    AuditModule,
    AuthorizationModule,
    CircuitBreakerModule,
    DLPModule,
    RateLimitModule,
)
from mas_gateway.circuit_breaker import CircuitStatus
from pydantic import TypeAdapter

from .errors import (
    FailedPreconditionError,
    PermissionDeniedError,
    ResourceExhaustedError,
    RpcError,
)
from .routing import CorrelationCommit, MessageRouter

_ADMISSION_RESULTS = TypeAdapter(tuple[object, object])


class PolicyPipeline:
    """Run policy checks, route, and audit."""

    def __init__(
        self,
        *,
        authz: AuthorizationModule,
        rate_limit: RateLimitModule,
        audit: AuditModule,
        router: MessageRouter,
        dlp: DLPModule | None,
        circuit_breaker: CircuitBreakerModule | None,
    ) -> None:
        """Initialize policy pipeline."""
        self._authz = authz
        self._rate_limit = rate_limit
        self._audit = audit
        self._router = router
        self._atomic_enqueue = audit.commit_target.compatible_with(router.commit_target)
        self._dlp = dlp
        self._circuit_breaker = circuit_breaker

    async def ingest_and_route(
        self,
        message: EnvelopeMessage,
        *,
        correlation: CorrelationCommit | None = None,
    ) -> str:
        """Run policy checks, route the message, and emit audit entry."""
        start = time.perf_counter()
        telemetry = get_telemetry()

        with telemetry.start_span(
            "mas.server.policy.ingest",
            kind=SpanKind.INTERNAL,
            attributes={
                "mas.sender_id": message.sender_id,
                "mas.target_id": message.target_id,
                "mas.message_type": message.message_type,
                "mas.is_reply": message.meta.is_reply,
            },
        ) as span:

            async def log_and_raise(
                decision: str, violations: list[str], exc: RpcError
            ) -> None:
                latency_ms = (time.perf_counter() - start) * 1000
                telemetry.record_ingress(decision=decision)
                telemetry.record_policy_latency(
                    latency_ms=latency_ms, decision=decision
                )
                await self._audit.log_message(
                    message.message_id,
                    message.sender_id,
                    message.target_id,
                    decision,
                    latency_ms,
                    message.data,
                    violations=violations,
                    message_type=message.message_type,
                    correlation_id=message.meta.correlation_id,
                    sender_instance_id=message.meta.sender_instance_id,
                )
                span.record_exception(exc)
                span.set_attribute("mas.decision", decision)
                raise exc

            authorized = await self._authz.authorize(
                message.sender_id, message.target_id, action="send"
            )
            if not authorized:
                await log_and_raise(
                    "AUTHZ_DENIED",
                    ["authorization_denied"],
                    PermissionDeniedError("not_authorized"),
                )

            status: CircuitStatus | None = None
            if (
                self._circuit_breaker is not None
                and self._circuit_breaker.redis.connection_pool
                is self._rate_limit.redis.connection_pool
            ):
                with telemetry.start_span("mas.server.policy.check_admission"):
                    async with self._rate_limit.redis.pipeline(
                        transaction=False
                    ) as pipeline:
                        self._rate_limit.queue_check(
                            pipeline, message.sender_id, message.message_id
                        )
                        self._circuit_breaker.queue_check(pipeline, message.target_id)
                        raw_rate, raw_circuit = _ADMISSION_RESULTS.validate_python(
                            await pipeline.execute(raise_on_error=False)
                        )
                    rate = self._rate_limit.parse_result(raw_rate)
                    if rate.allowed:
                        status = await self._circuit_breaker.resolve_check(
                            raw_circuit, message.target_id
                        )
            else:
                rate = await self._rate_limit.check_rate_limit(
                    message.sender_id, message.message_id
                )
            if not rate.allowed:
                await log_and_raise(
                    "RATE_LIMITED",
                    ["rate_limit_exceeded"],
                    ResourceExhaustedError("rate_limited"),
                )

            if self._circuit_breaker:
                if status is None:
                    status = await self._circuit_breaker.check_circuit(
                        message.target_id
                    )
                if not status.allowed:
                    await log_and_raise(
                        "CIRCUIT_OPEN",
                        ["circuit_open"],
                        FailedPreconditionError("circuit_open"),
                    )

            decision = "ALLOWED"
            violations: list[str] = []
            if self._dlp:
                scan = await self._dlp.scan(message.data)
                if not scan.clean:
                    violations.extend(
                        [entry.violation_type for entry in scan.violations]
                    )

                    if scan.action == ActionPolicy.BLOCK:
                        await log_and_raise(
                            "DLP_BLOCKED",
                            violations,
                            PermissionDeniedError("dlp_blocked"),
                        )

                    if scan.action == ActionPolicy.ALERT:
                        decision = "ALERT"
                    elif scan.action == ActionPolicy.REDACT:
                        decision = "DLP_REDACTED"

                    if (
                        scan.action == ActionPolicy.REDACT
                        and scan.redacted_payload is not None
                    ):
                        message.data = scan.redacted_payload

            latency_ms = (time.perf_counter() - start) * 1000
            stream_append = (
                self._router.prepare_stream_append(message)
                if self._atomic_enqueue
                and correlation is None
                and not message.meta.is_reply
                else None
            )
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                decision,
                latency_ms,
                message.data,
                violations=violations,
                message_type=message.message_type,
                correlation_id=message.meta.correlation_id,
                sender_instance_id=message.meta.sender_instance_id,
                stream_append=stream_append,
            )
            span.set_attribute("mas.decision", decision)
            if correlation is not None:
                committed_id = await self._router.commit_reply(message, correlation)
            else:
                if stream_append is None:
                    await self._router.route_message(message)
                committed_id = message.message_id
            telemetry.record_ingress(decision=decision)
            telemetry.record_policy_latency(
                latency_ms=(time.perf_counter() - start) * 1000, decision=decision
            )
            return committed_id

    async def replay_reply(
        self, *, correlation_id: str, sender_id: str, fingerprint: str
    ) -> str | None:
        """Return only a committed receipt matching the authenticated reply intent."""
        return await self._router.replay_reply(
            correlation_id=correlation_id,
            sender_id=sender_id,
            fingerprint=fingerprint,
        )
