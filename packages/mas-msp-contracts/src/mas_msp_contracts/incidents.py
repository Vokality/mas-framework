"""Contracts for incident workflows, diagnostics, and remediations."""

from __future__ import annotations

from pydantic import Field, PositiveInt

from ._base import ContractModel
from .assets import AssetRef
from .enums import IncidentState, Severity
from .types import (
    ApprovalId,
    AssetId,
    ClientId,
    EvidenceBundleId,
    FabricId,
    IncidentId,
    JSONObject,
    NonEmptyStr,
    RequestId,
    UTCDatetime,
)


class DiagnosticsCollect(ContractModel):
    """Deterministic diagnostics request."""

    request_id: RequestId
    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    diagnostic_profile: NonEmptyStr
    requested_actions: list[NonEmptyStr] = Field(default_factory=list)
    timeout_seconds: PositiveInt
    read_only: bool


class DiagnosticsResult(ContractModel):
    """Deterministic diagnostics response."""

    request_id: RequestId
    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    completed_at: UTCDatetime
    outcome: NonEmptyStr
    evidence_bundle_id: EvidenceBundleId | None = None
    observations: list[JSONObject] = Field(default_factory=list)
    structured_results: JSONObject = Field(default_factory=dict)


class RemediationExecute(ContractModel):
    """Deterministic executor request."""

    request_id: RequestId
    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    action_type: NonEmptyStr
    parameters: JSONObject = Field(default_factory=dict)
    approval_id: ApprovalId


class RemediationResult(ContractModel):
    """Deterministic executor response."""

    request_id: RequestId
    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    completed_at: UTCDatetime
    outcome: NonEmptyStr
    audit_reference: NonEmptyStr | None = None
    post_state: JSONObject = Field(default_factory=dict)


class IncidentRecord(ContractModel):
    """Persisted incident envelope."""

    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    correlation_key: NonEmptyStr | None = None
    state: IncidentState
    severity: Severity
    summary: NonEmptyStr
    asset_ids: list[AssetId] = Field(default_factory=list)
    opened_at: UTCDatetime
    updated_at: UTCDatetime


class EvidenceBundle(ContractModel):
    """Structured evidence collected for an incident."""

    evidence_bundle_id: EvidenceBundleId
    incident_id: IncidentId
    asset_id: AssetId
    collected_at: UTCDatetime
    items: list[JSONObject] = Field(default_factory=list)
    summary: NonEmptyStr


__all__ = [
    "DiagnosticsCollect",
    "DiagnosticsResult",
    "EvidenceBundle",
    "IncidentRecord",
    "RemediationExecute",
    "RemediationResult",
]
