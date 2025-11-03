"""Prometheus Metrics Module for Gateway Service.

Provides comprehensive metrics collection for monitoring and alerting:
- Counters: Total messages, failures, violations
- Histograms: Latency distributions, duration metrics
- Gauges: Active connections, queue depths, circuit breaker states

Metrics are exposed in Prometheus format for scraping.
"""

import logging
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

logger = logging.getLogger(__name__)


# =============================================================================
# COUNTER METRICS
# =============================================================================

# Total messages processed
gateway_messages_total = Counter(
    "gateway_messages_total",
    "Total number of messages processed by gateway",
    [
        "decision"
    ],  # Labels: ALLOWED, AUTH_FAILED, AUTHZ_DENIED, RATE_LIMITED, DLP_BLOCKED
)

# Authentication metrics
gateway_auth_failures_total = Counter(
    "gateway_auth_failures_total",
    "Total number of authentication failures",
    ["reason"],  # Labels: invalid_token, agent_not_found, token_expired
)

# Authorization metrics
gateway_authz_denied_total = Counter(
    "gateway_authz_denied_total",
    "Total number of authorization denials",
    ["sender", "target"],  # Labels: sender agent, target agent
)

# Rate limiting metrics
gateway_rate_limited_total = Counter(
    "gateway_rate_limited_total",
    "Total number of rate limit violations",
    ["agent_id"],  # Labels: agent that hit rate limit
)

# DLP violation metrics
gateway_dlp_violations_total = Counter(
    "gateway_dlp_violations_total",
    "Total number of DLP violations detected",
    [
        "violation_type",
        "action",
    ],  # Labels: PII_SSN, PCI_CREDIT_CARD, etc. / BLOCK, REDACT, ALERT
)

# Security event metrics
gateway_security_events_total = Counter(
    "gateway_security_events_total",
    "Total number of security events logged",
    ["event_type"],  # Labels: AUTH_FAILURE, AUTHZ_DENIED, DLP_VIOLATION
)

# Circuit breaker metrics
gateway_circuit_breaker_trips_total = Counter(
    "gateway_circuit_breaker_trips_total",
    "Total number of circuit breaker trips",
    ["target_id", "state"],  # Labels: target agent, OPEN/HALF_OPEN/CLOSED
)

# =============================================================================
# HISTOGRAM METRICS
# =============================================================================

# Message processing latency
gateway_message_latency_seconds = Histogram(
    "gateway_message_latency_seconds",
    "Message processing latency in seconds",
    ["decision"],  # Labels: ALLOWED, DENIED, etc.
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
    ),
)

# DLP scan duration
gateway_dlp_scan_duration_seconds = Histogram(
    "gateway_dlp_scan_duration_seconds",
    "DLP scan duration in seconds",
    buckets=(0.001, 0.0025, 0.005, 0.0075, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)

# Authentication duration
gateway_auth_duration_seconds = Histogram(
    "gateway_auth_duration_seconds",
    "Authentication check duration in seconds",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)

# Authorization duration
gateway_authz_duration_seconds = Histogram(
    "gateway_authz_duration_seconds",
    "Authorization check duration in seconds",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)

# =============================================================================
# GAUGE METRICS
# =============================================================================

# Active connections (currently processing messages)
gateway_active_requests = Gauge(
    "gateway_active_requests",
    "Number of requests currently being processed",
)

# Circuit breaker state
gateway_circuit_breaker_state = Gauge(
    "gateway_circuit_breaker_state",
    "Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
    ["target_id"],  # Labels: target agent ID
)

# Priority queue depth
gateway_queue_depth = Gauge(
    "gateway_queue_depth",
    "Number of messages in priority queue",
    ["target_id", "priority"],  # Labels: target agent, priority level
)

# Audit stream length
gateway_audit_stream_length = Gauge(
    "gateway_audit_stream_length",
    "Number of entries in audit stream",
    ["stream_type"],  # Labels: messages, security_events
)

# =============================================================================
# INFO METRICS
# =============================================================================

# Gateway info
gateway_info = Info(
    "gateway",
    "Gateway service information",
)


class MetricsCollector:
    """
    Metrics collector for Gateway service.

    Provides helper methods to record metrics throughout the gateway
    message processing pipeline.
    """

    @staticmethod
    def record_message(decision: str, latency_seconds: float) -> None:
        """
        Record message processing metrics.

        Args:
            decision: Gateway decision (ALLOWED, DENIED, etc.)
            latency_seconds: Processing latency in seconds
        """
        gateway_messages_total.labels(decision=decision).inc()
        gateway_message_latency_seconds.labels(decision=decision).observe(
            latency_seconds
        )

    @staticmethod
    def record_auth_failure(reason: str) -> None:
        """
        Record authentication failure.

        Args:
            reason: Failure reason (invalid_token, agent_not_found, etc.)
        """
        gateway_auth_failures_total.labels(reason=reason).inc()
        gateway_security_events_total.labels(event_type="AUTH_FAILURE").inc()

    @staticmethod
    def record_authz_denied(sender: str, target: str) -> None:
        """
        Record authorization denial.

        Args:
            sender: Sender agent ID
            target: Target agent ID
        """
        gateway_authz_denied_total.labels(sender=sender, target=target).inc()
        gateway_security_events_total.labels(event_type="AUTHZ_DENIED").inc()

    @staticmethod
    def record_rate_limited(agent_id: str) -> None:
        """
        Record rate limit violation.

        Args:
            agent_id: Agent that hit rate limit
        """
        gateway_rate_limited_total.labels(agent_id=agent_id).inc()

    @staticmethod
    def record_dlp_violation(violation_type: str, action: str = "BLOCK") -> None:
        """
        Record DLP violation.

        Args:
            violation_type: Type of violation (PII_SSN, PCI_CREDIT_CARD, etc.)
            action: Action taken (BLOCK, REDACT, ALERT, ENCRYPT)
        """
        gateway_dlp_violations_total.labels(
            violation_type=violation_type, action=action
        ).inc()
        gateway_security_events_total.labels(event_type="DLP_VIOLATION").inc()

    @staticmethod
    def record_dlp_scan_duration(duration_seconds: float) -> None:
        """
        Record DLP scan duration.

        Args:
            duration_seconds: Scan duration in seconds
        """
        gateway_dlp_scan_duration_seconds.observe(duration_seconds)

    @staticmethod
    def record_auth_duration(duration_seconds: float) -> None:
        """
        Record authentication duration.

        Args:
            duration_seconds: Auth duration in seconds
        """
        gateway_auth_duration_seconds.observe(duration_seconds)

    @staticmethod
    def record_authz_duration(duration_seconds: float) -> None:
        """
        Record authorization duration.

        Args:
            duration_seconds: Authz duration in seconds
        """
        gateway_authz_duration_seconds.observe(duration_seconds)

    @staticmethod
    def record_circuit_breaker_trip(target_id: str, state: str) -> None:
        """
        Record circuit breaker state change.

        Args:
            target_id: Target agent ID
            state: New state (OPEN, CLOSED, HALF_OPEN)
        """
        gateway_circuit_breaker_trips_total.labels(
            target_id=target_id, state=state
        ).inc()

        # Update gauge with numeric state value
        state_map = {"CLOSED": 0, "OPEN": 1, "HALF_OPEN": 2}
        gateway_circuit_breaker_state.labels(target_id=target_id).set(
            state_map.get(state, 0)
        )

    @staticmethod
    def set_queue_depth(target_id: str, priority: str, depth: int) -> None:
        """
        Set priority queue depth gauge.

        Args:
            target_id: Target agent ID
            priority: Priority level (CRITICAL, HIGH, NORMAL, LOW, BULK)
            depth: Queue depth
        """
        gateway_queue_depth.labels(target_id=target_id, priority=priority).set(depth)

    @staticmethod
    def set_audit_stream_length(stream_type: str, length: int) -> None:
        """
        Set audit stream length gauge.

        Args:
            stream_type: Stream type (messages, security_events)
            length: Stream length
        """
        gateway_audit_stream_length.labels(stream_type=stream_type).set(length)

    @staticmethod
    def increment_active_requests() -> None:
        """Increment active requests gauge."""
        gateway_active_requests.inc()

    @staticmethod
    def decrement_active_requests() -> None:
        """Decrement active requests gauge."""
        gateway_active_requests.dec()

    @staticmethod
    def set_gateway_info(
        version: str, dlp_enabled: bool, priority_queue_enabled: bool
    ) -> None:
        """
        Set gateway information.

        Args:
            version: Gateway version
            dlp_enabled: Whether DLP is enabled
            priority_queue_enabled: Whether priority queue is enabled
        """
        gateway_info.info(
            {
                "version": version,
                "dlp_enabled": str(dlp_enabled),
                "priority_queue_enabled": str(priority_queue_enabled),
            }
        )


def get_metrics() -> bytes:
    """
    Get metrics in Prometheus format.

    Returns:
        Metrics as bytes in Prometheus text format
    """
    return generate_latest(REGISTRY)


def get_content_type() -> str:
    """
    Get Prometheus metrics content type.

    Returns:
        Content type string
    """
    return CONTENT_TYPE_LATEST
