"""Typed dependency objects for Phase 3 incident reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas_msp_contracts import AssetRef, EvidenceBundle, IncidentRecord, JSONObject


@dataclass(frozen=True, slots=True)
class TriageDeps:
    """Inputs used to decide whether an incident needs more diagnostics."""

    client_id: str
    fabric_id: str
    incident_id: str
    incident_record: IncidentRecord
    asset_refs: tuple[AssetRef, ...] = field(default_factory=tuple)
    recent_activity: tuple[JSONObject, ...] = field(default_factory=tuple)
    recent_evidence: tuple[EvidenceBundle, ...] = field(default_factory=tuple)
    user_intent: str = ""
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DiagnosticsPlannerDeps:
    """Inputs used to build a read-only diagnostics plan."""

    incident_id: str
    asset_refs: tuple[AssetRef, ...] = field(default_factory=tuple)
    recent_evidence: tuple[EvidenceBundle, ...] = field(default_factory=tuple)
    available_profiles: tuple[str, ...] = field(default_factory=tuple)
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SummaryComposerDeps:
    """Inputs used to draft an operator-facing incident summary."""

    incident_id: str
    incident_record: IncidentRecord
    asset_refs: tuple[AssetRef, ...] = field(default_factory=tuple)
    recent_evidence: tuple[EvidenceBundle, ...] = field(default_factory=tuple)
    recent_activity: tuple[JSONObject, ...] = field(default_factory=tuple)


__all__ = [
    "DiagnosticsPlannerDeps",
    "SummaryComposerDeps",
    "TriageDeps",
]
