"""Read-only Phase 3 diagnostics for supported network devices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from mas_msp_contracts import DiagnosticsCollect, DiagnosticsResult, EvidenceBundle
from mas_msp_contracts.types import JSONObject


@dataclass(frozen=True, slots=True)
class DiagnosticsExecution:
    """Combined diagnostics result and evidence bundle."""

    result: DiagnosticsResult
    evidence_bundle: EvidenceBundle


class NetworkDiagnosticsAgent:
    """Execute deterministic read-only diagnostics for network incidents."""

    async def execute_diagnostics(
        self,
        request: DiagnosticsCollect,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> DiagnosticsExecution:
        """Execute one deterministic diagnostics request."""

        observed_at = datetime.now(UTC)
        activity = recent_activity or []
        summary = self._summary(request, activity)
        evidence_bundle = EvidenceBundle(
            evidence_bundle_id=str(uuid4()),
            incident_id=request.incident_id,
            asset_id=request.asset.asset_id,
            collected_at=observed_at,
            items=self._items(request, activity),
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
            observations=evidence_bundle.items,
            structured_results={
                "diagnostic_profile": request.diagnostic_profile,
                "read_only": request.read_only,
                "requested_actions": list(request.requested_actions),
            },
        )
        return DiagnosticsExecution(result=result, evidence_bundle=evidence_bundle)

    def _summary(
        self,
        request: DiagnosticsCollect,
        activity: list[JSONObject],
    ) -> str:
        hostname = (
            request.asset.hostname
            or request.asset.mgmt_address
            or request.asset.asset_id
        )
        if activity:
            return f"Read-only diagnostics captured additional evidence for {hostname}."
        return f"Read-only diagnostics completed for {hostname}."

    def _items(
        self,
        request: DiagnosticsCollect,
        activity: list[JSONObject],
    ) -> list[JSONObject]:
        items: list[JSONObject] = [
            {
                "kind": "diagnostic_profile",
                "profile": request.diagnostic_profile,
                "read_only": request.read_only,
            },
            {
                "kind": "asset_identity",
                "hostname": request.asset.hostname,
                "mgmt_address": request.asset.mgmt_address,
                "vendor": request.asset.vendor,
                "model": request.asset.model,
            },
        ]
        if activity:
            items.append(
                {
                    "kind": "recent_activity",
                    "latest_event_type": activity[-1].get("event_type"),
                    "latest_payload": activity[-1].get("payload"),
                }
            )
        if "uplink" in request.diagnostic_profile:
            items.append(
                {
                    "kind": "interface_status",
                    "interface": "Gi1/0/48",
                    "state": "down",
                }
            )
        return items


__all__ = ["DiagnosticsExecution", "NetworkDiagnosticsAgent"]
