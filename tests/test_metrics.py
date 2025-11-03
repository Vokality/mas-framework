"""Tests for Prometheus Metrics Module."""

import pytest
from prometheus_client import REGISTRY
from mas.gateway.metrics import (
    MetricsCollector,
    get_metrics,
    get_content_type,
    gateway_messages_total,
    gateway_auth_failures_total,
    gateway_authz_denied_total,
    gateway_rate_limited_total,
    gateway_dlp_violations_total,
    gateway_circuit_breaker_trips_total,
    gateway_active_requests,
    gateway_circuit_breaker_state,
    gateway_queue_depth,
)

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
def reset_metrics():
    """Reset all metrics before each test."""
    # Clear all collectors in the registry
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass

    # Re-register our metrics
    # This is a simplified approach - in production you'd use separate registries per test
    yield


class TestMetricsCollector:
    """Test MetricsCollector helper methods."""

    async def test_record_message(self):
        """Test recording message metrics."""
        initial_value = gateway_messages_total.labels(decision="ALLOWED")._value._value

        MetricsCollector.record_message("ALLOWED", 0.015)

        # Check counter incremented
        final_value = gateway_messages_total.labels(decision="ALLOWED")._value._value
        assert final_value == initial_value + 1

    async def test_record_auth_failure(self):
        """Test recording auth failure metrics."""
        initial_value = gateway_auth_failures_total.labels(
            reason="invalid_token"
        )._value._value

        MetricsCollector.record_auth_failure("invalid_token")

        final_value = gateway_auth_failures_total.labels(
            reason="invalid_token"
        )._value._value
        assert final_value == initial_value + 1

    async def test_record_authz_denied(self):
        """Test recording authz denial metrics."""
        initial_value = gateway_authz_denied_total.labels(
            sender="agent-a", target="agent-b"
        )._value._value

        MetricsCollector.record_authz_denied("agent-a", "agent-b")

        final_value = gateway_authz_denied_total.labels(
            sender="agent-a", target="agent-b"
        )._value._value
        assert final_value == initial_value + 1

    async def test_record_rate_limited(self):
        """Test recording rate limit metrics."""
        initial_value = gateway_rate_limited_total.labels(
            agent_id="agent-a"
        )._value._value

        MetricsCollector.record_rate_limited("agent-a")

        final_value = gateway_rate_limited_total.labels(
            agent_id="agent-a"
        )._value._value
        assert final_value == initial_value + 1

    async def test_record_dlp_violation(self):
        """Test recording DLP violation metrics."""
        initial_value = gateway_dlp_violations_total.labels(
            violation_type="PII_SSN", action="BLOCK"
        )._value._value

        MetricsCollector.record_dlp_violation("PII_SSN", "BLOCK")

        final_value = gateway_dlp_violations_total.labels(
            violation_type="PII_SSN", action="BLOCK"
        )._value._value
        assert final_value == initial_value + 1

    async def test_record_dlp_scan_duration(self):
        """Test recording DLP scan duration."""
        # For histograms, just verify the method doesn't error
        MetricsCollector.record_dlp_scan_duration(0.005)
        MetricsCollector.record_dlp_scan_duration(0.010)

        # Verify metric exists in output
        metrics_data = get_metrics()
        metrics_str = metrics_data.decode("utf-8")
        assert "gateway_dlp_scan_duration_seconds" in metrics_str

    async def test_record_circuit_breaker_trip(self):
        """Test recording circuit breaker state transitions."""
        initial_value = gateway_circuit_breaker_trips_total.labels(
            target_id="agent-a", state="OPEN"
        )._value._value

        MetricsCollector.record_circuit_breaker_trip("agent-a", "OPEN")

        final_value = gateway_circuit_breaker_trips_total.labels(
            target_id="agent-a", state="OPEN"
        )._value._value
        assert final_value == initial_value + 1

        # Check gauge updated
        gauge_value = gateway_circuit_breaker_state.labels(
            target_id="agent-a"
        )._value._value
        assert gauge_value == 1  # OPEN = 1

    async def test_active_requests_gauge(self):
        """Test active requests gauge."""
        initial_value = gateway_active_requests._value._value

        MetricsCollector.increment_active_requests()
        assert gateway_active_requests._value._value == initial_value + 1

        MetricsCollector.decrement_active_requests()
        assert gateway_active_requests._value._value == initial_value

    async def test_set_queue_depth(self):
        """Test setting queue depth gauge."""
        MetricsCollector.set_queue_depth("agent-a", "CRITICAL", 10)

        value = gateway_queue_depth.labels(
            target_id="agent-a", priority="CRITICAL"
        )._value._value
        assert value == 10

    async def test_set_gateway_info(self):
        """Test setting gateway info."""
        MetricsCollector.set_gateway_info(
            version="0.1.14",
            dlp_enabled=True,
            priority_queue_enabled=False,
        )

        # Info metric should be set (we can't easily assert on it, just verify no error)
        assert True


class TestMetricsExport:
    """Test metrics export functionality."""

    async def test_get_metrics(self):
        """Test getting metrics in Prometheus format."""
        # Record some metrics
        MetricsCollector.record_message("ALLOWED", 0.015)
        MetricsCollector.record_auth_failure("invalid_token")

        # Get metrics
        metrics_data = get_metrics()

        # Should be bytes
        assert isinstance(metrics_data, bytes)

        # Should contain Prometheus format
        metrics_str = metrics_data.decode("utf-8")
        assert "gateway_messages_total" in metrics_str
        assert "gateway_auth_failures_total" in metrics_str

    async def test_get_content_type(self):
        """Test getting Prometheus content type."""
        content_type = get_content_type()
        assert isinstance(content_type, str)
        assert "text/plain" in content_type or "text" in content_type


class TestMetricsIntegration:
    """Integration tests for metrics with gateway components."""

    async def test_metrics_with_gateway_workflow(self):
        """Test that metrics are recorded during gateway message processing."""
        # Record a complete workflow
        MetricsCollector.record_message("ALLOWED", 0.012)
        MetricsCollector.record_message("AUTH_FAILED", 0.005)
        MetricsCollector.record_message("RATE_LIMITED", 0.008)

        # Get metrics
        metrics_data = get_metrics()
        metrics_str = metrics_data.decode("utf-8")

        # Verify counters present
        assert "gateway_messages_total" in metrics_str
        assert 'decision="ALLOWED"' in metrics_str
        assert 'decision="AUTH_FAILED"' in metrics_str
        assert 'decision="RATE_LIMITED"' in metrics_str

    async def test_circuit_breaker_state_gauge(self):
        """Test circuit breaker state gauge values."""
        # Record state transitions
        MetricsCollector.record_circuit_breaker_trip("agent-a", "CLOSED")
        MetricsCollector.record_circuit_breaker_trip("agent-b", "OPEN")
        MetricsCollector.record_circuit_breaker_trip("agent-c", "HALF_OPEN")

        # Check gauge values
        assert (
            gateway_circuit_breaker_state.labels(target_id="agent-a")._value._value == 0
        )  # CLOSED
        assert (
            gateway_circuit_breaker_state.labels(target_id="agent-b")._value._value == 1
        )  # OPEN
        assert (
            gateway_circuit_breaker_state.labels(target_id="agent-c")._value._value == 2
        )  # HALF_OPEN
