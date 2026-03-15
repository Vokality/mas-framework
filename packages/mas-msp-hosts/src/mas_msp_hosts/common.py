"""Shared helpers for Phase 5 host visibility, diagnostics, and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from mas_msp_contracts import (
    AssetKind,
    AssetRef,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    HealthState,
)
from mas_msp_contracts.types import JSONObject


HOST_SERVICE_ACTIONS = frozenset({"service.start", "service.stop", "service.restart"})


def build_host_asset_ref(
    *,
    asset_kind: AssetKind,
    client_id: str,
    fabric_id: str,
    vendor: str,
    model: str | None,
    hostname: str | None,
    mgmt_address: str | None,
    site: str | None,
    tags: list[str],
) -> AssetRef:
    """Return a candidate host asset reference for inventory binding."""

    return AssetRef(
        asset_id=str(uuid4()),
        client_id=client_id,
        fabric_id=fabric_id,
        asset_kind=asset_kind,
        vendor=vendor,
        model=model,
        hostname=hostname,
        mgmt_address=mgmt_address,
        site=site,
        tags=tags,
    )


def evaluate_host_health(
    *,
    metrics: dict[str, object],
    services: list[dict[str, object]],
    findings: list[JSONObject] | None = None,
) -> tuple[HealthState, list[JSONObject]]:
    """Derive a host health state from normalized metrics and service states."""

    resolved_findings = [dict(item) for item in findings or []]
    health_state = HealthState.HEALTHY
    for metric_name, warning, critical in (
        ("cpu_percent", 80.0, 95.0),
        ("memory_percent", 75.0, 90.0),
        ("disk_percent", 85.0, 95.0),
    ):
        metric_value = _numeric_metric(metrics.get(metric_name))
        if metric_value is None:
            continue
        if metric_value >= critical:
            resolved_findings.append(
                {
                    "code": f"{metric_name}_critical",
                    "metric": metric_name,
                    "value": metric_value,
                }
            )
            health_state = HealthState.CRITICAL
            continue
        if metric_value >= warning and health_state is HealthState.HEALTHY:
            resolved_findings.append(
                {
                    "code": f"{metric_name}_warning",
                    "metric": metric_name,
                    "value": metric_value,
                }
            )
            health_state = HealthState.DEGRADED
    for service in services:
        service_name = service.get("service_name")
        service_state = service.get("service_state")
        if not isinstance(service_name, str) or not isinstance(service_state, str):
            continue
        if service_state != "running":
            resolved_findings.append(
                {
                    "code": "service_not_running",
                    "service_name": service_name,
                    "service_state": service_state,
                }
            )
            health_state = HealthState.CRITICAL
    return health_state, resolved_findings


def build_diagnostics_execution(
    request: DiagnosticsCollect,
    *,
    summary: str,
    items: list[JSONObject],
    structured_results: JSONObject,
) -> HostDiagnosticsExecution:
    """Build a deterministic diagnostics result and evidence bundle."""

    observed_at = datetime.now(UTC)
    evidence_bundle = EvidenceBundle(
        evidence_bundle_id=str(uuid4()),
        incident_id=request.incident_id,
        asset_id=request.asset.asset_id,
        collected_at=observed_at,
        items=items,
        summary=summary,
    )
    result = DiagnosticsResult(
        request_id=request.request_id,
        incident_id=request.incident_id,
        client_id=request.client_id,
        fabric_id=request.fabric_id,
        asset=request.asset,
        completed_at=observed_at,
        outcome="completed",
        evidence_bundle_id=evidence_bundle.evidence_bundle_id,
        observations=items,
        structured_results=structured_results,
    )
    return HostDiagnosticsExecution(result=result, evidence_bundle=evidence_bundle)


def extract_service_name(recent_activity: list[JSONObject] | None) -> str | None:
    """Infer the target service from recent incident activity when possible."""

    if not recent_activity:
        return None
    for activity in reversed(recent_activity):
        payload = activity.get("payload")
        if not isinstance(payload, dict):
            continue
        for candidate in (
            payload.get("service_name"),
            _nested_service_name(payload.get("alert")),
            _nested_service_name(payload.get("snapshot")),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


@dataclass(frozen=True, slots=True)
class HostDiagnosticsExecution:
    """Combined host diagnostics result and evidence bundle."""

    result: DiagnosticsResult
    evidence_bundle: EvidenceBundle


@dataclass(slots=True)
class HostServiceRegistry:
    """Deterministic host service state used by diagnostics and executors."""

    _services: dict[str, dict[str, str]] = field(default_factory=dict)

    def list_services(
        self,
        asset: AssetRef,
        *,
        hinted_service: str | None = None,
    ) -> list[dict[str, object]]:
        """Return the visible service states for one host asset."""

        service_state = self._service_map(asset, hinted_service=hinted_service)
        return [
            {"service_name": name, "service_state": state}
            for name, state in sorted(service_state.items())
        ]

    def apply_action(
        self,
        *,
        asset: AssetRef,
        action_type: str,
        service_name: str,
    ) -> dict[str, object]:
        """Apply a typed host service action and return the terminal state."""

        if action_type not in HOST_SERVICE_ACTIONS:
            raise ValueError(f"unsupported host action_type: {action_type}")
        services = self._service_map(asset, hinted_service=service_name)
        if action_type == "service.stop":
            services[service_name] = "stopped"
        else:
            services[service_name] = "running"
        return {
            "platform": asset.asset_kind.value,
            "service_name": service_name,
            "service_state": services[service_name],
        }

    def _service_map(
        self,
        asset: AssetRef,
        *,
        hinted_service: str | None,
    ) -> dict[str, str]:
        services = self._services.setdefault(
            asset.asset_id,
            _default_services(asset.asset_kind),
        )
        if hinted_service and hinted_service not in services:
            services[hinted_service] = "running"
        return services


def _default_services(asset_kind: AssetKind) -> dict[str, str]:
    if asset_kind is AssetKind.LINUX_HOST:
        return {"nginx": "failed", "sshd": "running"}
    if asset_kind is AssetKind.WINDOWS_HOST:
        return {"Spooler": "stopped", "W32Time": "running"}
    return {}


def _nested_service_name(value: object) -> str | None:
    record = _json_record(value)
    if record is None:
        return None
    normalized_facts = _json_record(record.get("normalized_facts"))
    if normalized_facts is not None:
        service_name = normalized_facts.get("service_name")
        if isinstance(service_name, str) and service_name.strip():
            return service_name.strip()
    services = _json_record(record.get("metrics"))
    if services is not None:
        service_name = services.get("service_name")
        if isinstance(service_name, str) and service_name.strip():
            return service_name.strip()
    return None


def _numeric_metric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _json_record(value: object) -> dict[str, object] | None:
    if value is None or not isinstance(value, dict):
        return None
    return {str(key): entry for key, entry in value.items()}


__all__ = [
    "HOST_SERVICE_ACTIONS",
    "HostDiagnosticsExecution",
    "HostServiceRegistry",
    "build_diagnostics_execution",
    "build_host_asset_ref",
    "evaluate_host_health",
    "extract_service_name",
]
