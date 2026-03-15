"""Typed PydanticAI outputs for Phase 3 incident reasoning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from mas_msp_contracts import ChatTurnState, JSONObject, NonEmptyStr, Severity


class TriageDecision(BaseModel):
    """Typed triage result for an incident chat turn."""

    incident_id: str
    summary: NonEmptyStr
    severity: Severity
    next_action: Literal["collect_diagnostics", "summarize"]
    diagnostic_profiles: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr


class DiagnosticsPlan(BaseModel):
    """Typed plan for deterministic read-only diagnostics."""

    incident_id: str
    steps: list[NonEmptyStr] = Field(default_factory=list)
    continue_in_background: bool
    operator_message: NonEmptyStr


class IncidentResponse(BaseModel):
    """Typed operator-facing response for one incident chat turn."""

    chat_session_id: str
    turn_id: str
    state: ChatTurnState
    markdown_summary: NonEmptyStr
    evidence_bundle_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[JSONObject] = Field(default_factory=list)
    approval_required: bool = False


class SummaryDraft(BaseModel):
    """Typed summary persisted back into the incident cockpit."""

    incident_id: str
    headline: NonEmptyStr
    operator_summary: NonEmptyStr
    affected_assets: list[NonEmptyStr] = Field(default_factory=list)
    recommended_actions: list[JSONObject] = Field(default_factory=list)


__all__ = [
    "DiagnosticsPlan",
    "IncidentResponse",
    "SummaryDraft",
    "TriageDecision",
]
