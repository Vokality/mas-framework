"""Security policy pipeline for message ingress."""

from __future__ import annotations

import time

from ..gateway.audit import AuditModule
from ..gateway.authorization import AuthorizationModule
from ..gateway.circuit_breaker import CircuitBreakerModule
from ..gateway.dlp import ActionPolicy, DLPModule
from ..gateway.rate_limit import RateLimitModule
from ..protocol import EnvelopeMessage
from ..telemetry import SpanKind, get_telemetry
from .errors import (
    FailedPreconditionError,
    PermissionDeniedError,
    ResourceExhaustedError,
    RpcError,
)
from .routing import MessageRouter


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
        self._dlp = dlp
        self._circuit_breaker = circuit_breaker

    async def ingest_and_route(self, message: EnvelopeMessage) -> None:
        """Run policy checks, route the message, and emit audit entry."""
        start = time.time()
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
                latency_ms = (time.time() - start) * 1000
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
                status = await self._circuit_breaker.check_circuit(message.target_id)
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
                    elif scan.action == ActionPolicy.ENCRYPT:
                        decision = "DLP_ENCRYPTED"

                    if scan.action == ActionPolicy.REDACT and scan.redacted_payload:
                        message.data = scan.redacted_payload

            await self._router.route_message(message)

            latency_ms = (time.time() - start) * 1000
            telemetry.record_ingress(decision=decision)
            telemetry.record_policy_latency(latency_ms=latency_ms, decision=decision)
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
            span.set_attribute("mas.decision", decision)
