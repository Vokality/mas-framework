"""Tool protocols exposed to the Phase 3 orchestrator."""

from __future__ import annotations

from typing import Protocol

from mas_msp_contracts import (
    ApprovalRequested,
    AssetRef,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    JSONObject,
    Severity,
)


class CoreOrchestratorToolset(Protocol):
    """Deterministic tools available to the incident orchestrator."""

    async def get_incident_context(self) -> tuple[IncidentRecord, list[JSONObject]]: ...

    async def get_asset_context(self) -> list[AssetRef]: ...

    async def get_recent_evidence(self) -> list[EvidenceBundle]: ...

    async def request_diagnostics(
        self,
        requests: list[DiagnosticsCollect],
    ) -> list[DiagnosticsResult]: ...

    async def append_activity(
        self,
        *,
        event_type: str,
        payload: JSONObject,
        asset_id: str | None,
    ) -> None: ...

    async def persist_summary(
        self,
        *,
        summary: str,
        severity: Severity,
        state: IncidentState,
        recommended_actions: list[JSONObject],
        asset_ids: list[str],
    ) -> IncidentRecord: ...

    async def request_approval(
        self,
        approval_request: ApprovalRequested,
    ) -> ApprovalRequested: ...


__all__ = ["CoreOrchestratorToolset"]
