"""Pure alert policy helpers for host evaluation and condition keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from mas_msp_contracts import (
    AlertRaised,
    AssetRef,
    HealthSnapshot,
    HealthState,
    Severity,
)
from mas_msp_contracts.types import JSONObject

from .models import AppliedAlertConfiguration, MetricThresholdPolicy


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    """One alertable condition derived from a host snapshot or source alert."""

    asset: AssetRef
    condition_key: str
    correlation_key: str
    category: str
    severity: Severity
    title: str
    source_kind: str
    normalized_facts: JSONObject
    open_after_polls: int
    close_after_polls: int
    cooldown_seconds: int


@dataclass(frozen=True, slots=True)
class HostSnapshotEvaluation:
    """Authoritative host snapshot plus the active alert candidates it implies."""

    authoritative_snapshot: HealthSnapshot
    active_candidates: list[AlertCandidate]


class AlertPolicyService:
    """Evaluate applied alert policy without touching persistence or transport."""

    def applied_or_default(
        self,
        configuration: AppliedAlertConfiguration | None,
        *,
        client_id: str,
    ) -> AppliedAlertConfiguration:
        if configuration is not None:
            return configuration
        return AppliedAlertConfiguration(client_id=client_id)

    def evaluate_host_snapshot(
        self,
        snapshot: HealthSnapshot,
        *,
        configuration: AppliedAlertConfiguration,
    ) -> HostSnapshotEvaluation:
        host_policy = configuration.resolve_host_policy(snapshot.asset)
        active_candidates: list[AlertCandidate] = []
        derived_findings: list[JSONObject] = []
        health_state = HealthState.HEALTHY
        for metric_name, metric_policy in (
            ("cpu_percent", host_policy.metrics.cpu_percent),
            ("memory_percent", host_policy.metrics.memory_percent),
            ("disk_percent", host_policy.metrics.disk_percent),
        ):
            metric_value = _numeric_metric(snapshot.metrics.get(metric_name))
            if metric_value is None:
                continue
            severity = _metric_severity(metric_value, metric_policy)
            if severity is None:
                continue
            derived_findings.append(
                {
                    "code": f"{metric_name}_{'critical' if severity is Severity.MAJOR else 'warning'}",
                    "metric": metric_name,
                    "value": metric_value,
                }
            )
            active_candidates.append(
                AlertCandidate(
                    asset=snapshot.asset,
                    condition_key=f"metric:{metric_name}",
                    correlation_key=build_correlation_key(
                        snapshot.asset, f"metric:{metric_name}"
                    ),
                    category="metric",
                    severity=severity,
                    title=(
                        f"{snapshot.asset.hostname or snapshot.asset.asset_id} "
                        f"{metric_name} is {metric_value:g}%"
                    ),
                    source_kind=snapshot.source_kind,
                    normalized_facts={
                        "metric": metric_name,
                        "value": metric_value,
                    },
                    open_after_polls=metric_policy.open_after_polls,
                    close_after_polls=metric_policy.close_after_polls,
                    cooldown_seconds=metric_policy.cooldown_seconds,
                )
            )
            if severity is Severity.MAJOR:
                health_state = HealthState.CRITICAL
            elif health_state is HealthState.HEALTHY:
                health_state = HealthState.DEGRADED
        watched_services = {
            service_name.lower() for service_name in host_policy.services.watch
        }
        if not watched_services:
            authoritative_snapshot = snapshot.model_copy(
                update={
                    "health_state": health_state,
                    "findings": [*list(snapshot.findings), *derived_findings],
                }
            )
            return HostSnapshotEvaluation(
                authoritative_snapshot=authoritative_snapshot,
                active_candidates=active_candidates,
            )
        for service in _service_states(snapshot.metrics):
            service_name = service.get("service_name")
            service_state = service.get("service_state")
            if not isinstance(service_name, str) or not isinstance(service_state, str):
                continue
            if service_name.lower() not in watched_services:
                continue
            if service_state == "running":
                continue
            derived_findings.append(
                {
                    "code": "service_not_running",
                    "service_name": service_name,
                    "service_state": service_state,
                }
            )
            active_candidates.append(
                AlertCandidate(
                    asset=snapshot.asset,
                    condition_key=f"service:{service_name}",
                    correlation_key=build_correlation_key(
                        snapshot.asset, f"service:{service_name}"
                    ),
                    category="service",
                    severity=Severity.MAJOR,
                    title=(
                        f"{service_name} on "
                        f"{snapshot.asset.hostname or snapshot.asset.asset_id} is {service_state}"
                    ),
                    source_kind=snapshot.source_kind,
                    normalized_facts={
                        "service_name": service_name,
                        "service_state": service_state,
                    },
                    open_after_polls=host_policy.services.open_after_polls,
                    close_after_polls=host_policy.services.close_after_polls,
                    cooldown_seconds=host_policy.services.cooldown_seconds,
                )
            )
            health_state = HealthState.CRITICAL
        authoritative_snapshot = snapshot.model_copy(
            update={
                "health_state": health_state,
                "findings": [*list(snapshot.findings), *derived_findings],
            }
        )
        return HostSnapshotEvaluation(
            authoritative_snapshot=authoritative_snapshot,
            active_candidates=active_candidates,
        )

    def source_alert_candidate(
        self,
        alert: AlertRaised,
        *,
        configuration: AppliedAlertConfiguration,
    ) -> AlertCandidate:
        condition_key = _source_condition_key(alert)
        correlation_key = _source_correlation_key(alert, condition_key=condition_key)
        return AlertCandidate(
            asset=alert.asset,
            condition_key=condition_key,
            correlation_key=correlation_key,
            category=alert.category,
            severity=alert.severity,
            title=alert.title,
            source_kind=alert.source_kind,
            normalized_facts=dict(alert.normalized_facts),
            open_after_polls=1,
            close_after_polls=1,
            cooldown_seconds=configuration.source_alerts.default_cooldown_seconds,
        )


def build_correlation_key(asset: AssetRef, condition_key: str) -> str:
    """Build the incident correlation key for one asset-scoped alert condition."""

    return f"{asset.asset_id}:{condition_key}"


def build_source_signature(alert: AlertRaised) -> str:
    """Return a stable signature for a source alert."""

    if alert.normalized_facts:
        canonical = json.dumps(
            alert.normalized_facts,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        canonical = alert.title.strip().lower()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_synthetic_alert(
    candidate: AlertCandidate,
    *,
    alert_id: str,
    occurred_at,
) -> AlertRaised:
    """Build a surfaced synthetic alert from a host condition candidate."""

    return AlertRaised(
        alert_id=alert_id,
        client_id=candidate.asset.client_id,
        fabric_id=candidate.asset.fabric_id,
        asset=candidate.asset,
        source_kind=candidate.source_kind,
        occurred_at=occurred_at,
        severity=candidate.severity,
        category=candidate.category,
        title=candidate.title,
        normalized_facts={
            **candidate.normalized_facts,
            "correlation_key": candidate.correlation_key,
            "condition_key": candidate.condition_key,
        },
    )


def _metric_severity(
    metric_value: float,
    policy: MetricThresholdPolicy,
) -> Severity | None:
    if metric_value >= policy.critical:
        return Severity.MAJOR
    if metric_value >= policy.warning:
        return Severity.WARNING
    return None


def _numeric_metric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _service_states(metrics: JSONObject) -> list[JSONObject]:
    services = metrics.get("services")
    if not isinstance(services, list):
        return []
    return [service for service in services if isinstance(service, dict)]


def _source_condition_key(alert: AlertRaised) -> str:
    service_name = alert.normalized_facts.get("service_name")
    if alert.category == "service" and isinstance(service_name, str) and service_name:
        return f"service:{service_name}"
    signature = build_source_signature(alert)
    return f"source:{alert.category}:{signature}"


def _source_correlation_key(alert: AlertRaised, *, condition_key: str) -> str:
    correlation_key = alert.normalized_facts.get("correlation_key")
    if isinstance(correlation_key, str) and correlation_key:
        return correlation_key
    return build_correlation_key(alert.asset, condition_key)


__all__ = [
    "AlertCandidate",
    "AlertPolicyService",
    "HostSnapshotEvaluation",
    "build_correlation_key",
    "build_source_signature",
    "build_synthetic_alert",
]
