"""Fabric-local incident handlers built on the phase-3 orchestrator."""

from __future__ import annotations

from typing import Protocol

from mas_msp_contracts import (
    AlertRaised,
    AssetRef,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    JSONObject,
    OperatorChatRequest,
    OperatorChatResponse,
    Severity,
)
from mas_msp_core import IncidentContextReader, NotifierTransportAgent

from .orchestrator import CoreOrchestratorAgent
from .toolsets import CoreOrchestratorToolset


class DiagnosticsExecution(Protocol):
    """Result envelope returned by a diagnostics executor."""

    result: DiagnosticsResult
    evidence_bundle: EvidenceBundle


class DiagnosticsExecutor(Protocol):
    """Execute one deterministic diagnostics request."""

    async def execute_diagnostics(
        self,
        request: DiagnosticsCollect,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> DiagnosticsExecution: ...


class FabricIncidentHandler:
    """Handle phase-3 incident chat and visibility inside one client fabric."""

    def __init__(
        self,
        *,
        incident_context_reader: IncidentContextReader,
        notifier: NotifierTransportAgent,
        orchestrator: CoreOrchestratorAgent,
        diagnostics_executor: DiagnosticsExecutor,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._notifier = notifier
        self._orchestrator = orchestrator
        self._diagnostics_executor = diagnostics_executor

    async def handle_incident_chat_request(
        self,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        """Dispatch one incident chat request through the core orchestrator."""

        toolset = _FabricIncidentToolset(
            incident_context_reader=self._incident_context_reader,
            notifier=self._notifier,
            diagnostics_executor=self._diagnostics_executor,
            request=request,
        )
        return await self._orchestrator.handle_chat_request(request, toolset=toolset)

    async def handle_visibility_alert(self, alert: AlertRaised) -> IncidentRecord:
        """Ensure visibility alerts are owned by the core orchestrator flow."""

        return await self._orchestrator.handle_visibility_alert(
            alert,
            notifier=self._notifier,
        )


class _FabricIncidentToolset(CoreOrchestratorToolset):
    """Adapter that exposes read and write ports to the orchestrator."""

    def __init__(
        self,
        *,
        incident_context_reader: IncidentContextReader,
        notifier: NotifierTransportAgent,
        diagnostics_executor: DiagnosticsExecutor,
        request: OperatorChatRequest,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._notifier = notifier
        self._diagnostics_executor = diagnostics_executor
        self._request = request

    async def get_incident_context(
        self,
    ) -> tuple[IncidentRecord, list[JSONObject]]:
        incident_id = self._require_incident_id()
        incident = await self._incident_context_reader.get_incident(incident_id)
        if incident is None:
            raise LookupError("incident was not found")
        activity = await self._incident_context_reader.list_activity_for_incident(
            incident_id
        )
        return incident, activity

    async def get_asset_context(self) -> list[AssetRef]:
        return await self._incident_context_reader.list_assets_for_incident(
            self._require_incident_id()
        )

    async def get_recent_evidence(self) -> list[EvidenceBundle]:
        return await self._incident_context_reader.list_evidence_for_incident(
            self._require_incident_id()
        )

    async def request_diagnostics(
        self,
        requests: list[DiagnosticsCollect],
    ) -> list[DiagnosticsResult]:
        results: list[DiagnosticsResult] = []
        for request in requests:
            activity = await self._incident_context_reader.list_activity_for_incident(
                request.incident_id
            )
            execution = await self._diagnostics_executor.execute_diagnostics(
                request,
                recent_activity=activity[-5:],
            )
            await self._notifier.persist_evidence_bundle(
                bundle=execution.evidence_bundle,
                client_id=request.client_id,
                fabric_id=request.fabric_id,
            )
            results.append(execution.result)
        return results

    async def append_activity(
        self,
        *,
        event_type: str,
        payload: JSONObject,
        asset_id: str | None,
    ) -> None:
        await self._notifier.append_activity(
            incident_id=self._require_incident_id(),
            event_type=event_type,
            payload=payload,
            asset_id=asset_id,
        )

    async def persist_summary(
        self,
        *,
        summary: str,
        severity: Severity,
        state: IncidentState,
        recommended_actions: list[JSONObject],
        asset_ids: list[str],
    ) -> IncidentRecord:
        return await self._notifier.persist_summary(
            incident_id=self._require_incident_id(),
            summary=summary,
            severity=severity,
            state=state,
            recommended_actions=recommended_actions,
            asset_ids=asset_ids,
        )

    def _require_incident_id(self) -> str:
        incident_id = self._request.incident_id
        if incident_id is None:
            raise LookupError("incident chat requires an incident_id")
        return incident_id


__all__ = ["DiagnosticsExecution", "DiagnosticsExecutor", "FabricIncidentHandler"]
