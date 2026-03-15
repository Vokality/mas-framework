"""Shared helpers for Phase 2 network normalization."""

from __future__ import annotations

from uuid import uuid4

from mas_msp_contracts import AssetKind, AssetRef, HealthState, Severity

from mas_msp_network.models import SnmpPollObservation


def build_candidate_asset_ref(
    *,
    client_id: str,
    fabric_id: str,
    vendor: str,
    model: str | None,
    hostname: str | None,
    mgmt_address: str | None,
    site: str | None,
    tags: list[str],
) -> AssetRef:
    """Return a candidate asset reference for inventory binding."""

    return AssetRef(
        asset_id=str(uuid4()),
        client_id=client_id,
        fabric_id=fabric_id,
        asset_kind=AssetKind.NETWORK_DEVICE,
        vendor=vendor,
        model=model,
        hostname=hostname,
        mgmt_address=mgmt_address,
        site=site,
        tags=tags,
    )


def map_cisco_syslog_severity(level: int) -> Severity:
    """Map Cisco syslog levels into the shared severity enum."""

    if level <= 1:
        return Severity.CRITICAL
    if level <= 3:
        return Severity.MAJOR
    if level == 4:
        return Severity.MINOR
    if level == 5:
        return Severity.WARNING
    return Severity.INFO


def map_textual_severity(level: str) -> Severity:
    """Map textual vendor levels into the shared severity enum."""

    normalized = level.strip().lower()
    if normalized in {"critical", "alert", "emergency"}:
        return Severity.CRITICAL
    if normalized in {"error", "err", "major"}:
        return Severity.MAJOR
    if normalized in {"warning", "warn", "minor"}:
        return Severity.WARNING
    return Severity.INFO


def evaluate_health(
    observation: SnmpPollObservation,
    *,
    cpu_warning: float,
    cpu_critical: float,
    memory_warning: float,
    memory_critical: float,
    extra_warning_metric: str | None = None,
    extra_warning_threshold: float | None = None,
    extra_critical_metric: str | None = None,
    extra_critical_threshold: float | None = None,
    critical_string_metric: tuple[str, str] | None = None,
) -> tuple[HealthState, list[dict[str, object]]]:
    """Derive a health state and findings from normalized poll metrics."""

    findings: list[dict[str, object]] = []
    health_state = HealthState.HEALTHY

    cpu_percent = _numeric_metric(observation.metrics, "cpu_percent")
    memory_percent = _numeric_metric(observation.metrics, "memory_percent")
    if cpu_percent is not None:
        if cpu_percent >= cpu_critical:
            findings.append(
                {"code": "cpu_critical", "metric": "cpu_percent", "value": cpu_percent}
            )
            health_state = HealthState.CRITICAL
        elif cpu_percent >= cpu_warning:
            findings.append(
                {"code": "cpu_warning", "metric": "cpu_percent", "value": cpu_percent}
            )
            health_state = HealthState.DEGRADED
    if memory_percent is not None:
        if memory_percent >= memory_critical:
            findings.append(
                {
                    "code": "memory_critical",
                    "metric": "memory_percent",
                    "value": memory_percent,
                }
            )
            health_state = HealthState.CRITICAL
        elif memory_percent >= memory_warning and health_state is HealthState.HEALTHY:
            findings.append(
                {
                    "code": "memory_warning",
                    "metric": "memory_percent",
                    "value": memory_percent,
                }
            )
            health_state = HealthState.DEGRADED

    if extra_critical_metric and extra_critical_threshold is not None:
        metric_value = _numeric_metric(observation.metrics, extra_critical_metric)
        if metric_value is not None and metric_value >= extra_critical_threshold:
            findings.append(
                {
                    "code": f"{extra_critical_metric}_critical",
                    "metric": extra_critical_metric,
                    "value": metric_value,
                }
            )
            health_state = HealthState.CRITICAL
    if extra_warning_metric and extra_warning_threshold is not None:
        metric_value = _numeric_metric(observation.metrics, extra_warning_metric)
        if metric_value is not None and metric_value >= extra_warning_threshold:
            findings.append(
                {
                    "code": f"{extra_warning_metric}_warning",
                    "metric": extra_warning_metric,
                    "value": metric_value,
                }
            )
            if health_state is HealthState.HEALTHY:
                health_state = HealthState.DEGRADED

    if critical_string_metric is not None:
        metric_name, expected_value = critical_string_metric
        metric_value = observation.metrics.get(metric_name)
        if (
            isinstance(metric_value, str)
            and metric_value.strip().lower() == expected_value
        ):
            findings.append(
                {
                    "code": f"{metric_name}_critical",
                    "metric": metric_name,
                    "value": metric_value,
                }
            )
            health_state = HealthState.CRITICAL

    return health_state, [*observation.findings, *findings]


def _numeric_metric(metrics: dict[str, object], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


__all__ = [
    "build_candidate_asset_ref",
    "evaluate_health",
    "map_cisco_syslog_severity",
    "map_textual_severity",
]
