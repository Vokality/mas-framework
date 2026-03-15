"""Incident remediation result models shared across fabric and ops-plane seams."""

from __future__ import annotations

from dataclasses import dataclass

from mas_msp_contracts import (
    DiagnosticsResult,
    EvidenceBundle,
    IncidentState,
    RemediationResult,
)


@dataclass(frozen=True, slots=True)
class IncidentRemediationExecution:
    """Terminal result for one approved incident remediation workflow."""

    remediation_result: RemediationResult
    verification_result: DiagnosticsResult | None
    verification_evidence_bundle: EvidenceBundle | None
    incident_state: IncidentState
    operator_message: str


__all__ = ["IncidentRemediationExecution"]
