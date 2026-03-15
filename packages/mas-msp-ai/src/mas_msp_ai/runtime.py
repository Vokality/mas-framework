"""Fabric-local incident handlers built on the phase-3 orchestrator."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from mas_msp_contracts import (
    ApprovalRequested,
    AlertRaised,
    AssetKind,
    AssetRef,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    JSONObject,
    OperatorChatRequest,
    OperatorChatResponse,
    RemediationExecute,
    RemediationResult,
    Severity,
)
from mas_msp_core import (
    ApprovalController,
    IncidentContextReader,
    IncidentRemediationExecution,
    IncidentRemediationHandler,
    NotifierTransportAgent,
)

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


class RemediationExecutor(Protocol):
    """Execute one deterministic remediation request."""

    async def execute_remediation(
        self,
        request: RemediationExecute,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> RemediationResult: ...


class AssetKindDiagnosticsExecutor(DiagnosticsExecutor):
    """Dispatch diagnostics to the platform-specific implementation."""

    def __init__(
        self,
        *,
        network_executor: DiagnosticsExecutor,
        linux_executor: DiagnosticsExecutor | None = None,
        windows_executor: DiagnosticsExecutor | None = None,
    ) -> None:
        self._network_executor = network_executor
        self._linux_executor = linux_executor
        self._windows_executor = windows_executor

    async def execute_diagnostics(
        self,
        request: DiagnosticsCollect,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> DiagnosticsExecution:
        executor = self._executor_for(request.asset.asset_kind)
        return await executor.execute_diagnostics(
            request,
            recent_activity=recent_activity,
        )

    def _executor_for(self, asset_kind: AssetKind) -> DiagnosticsExecutor:
        if asset_kind is AssetKind.NETWORK_DEVICE:
            return self._network_executor
        if asset_kind is AssetKind.LINUX_HOST:
            if self._linux_executor is None:
                raise LookupError("no Linux diagnostics executor is configured")
            return self._linux_executor
        if self._windows_executor is None:
            raise LookupError("no Windows diagnostics executor is configured")
        return self._windows_executor


class AssetKindRemediationExecutor(RemediationExecutor):
    """Dispatch remediations to the platform-specific implementation."""

    def __init__(
        self,
        *,
        linux_executor: RemediationExecutor | None = None,
        windows_executor: RemediationExecutor | None = None,
    ) -> None:
        self._linux_executor = linux_executor
        self._windows_executor = windows_executor

    async def execute_remediation(
        self,
        request: RemediationExecute,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> RemediationResult:
        executor = self._executor_for(request.asset.asset_kind)
        return await executor.execute_remediation(
            request,
            recent_activity=recent_activity,
        )

    def _executor_for(self, asset_kind: AssetKind) -> RemediationExecutor:
        if asset_kind is AssetKind.LINUX_HOST:
            if self._linux_executor is None:
                raise LookupError("no Linux remediation executor is configured")
            return self._linux_executor
        if asset_kind is AssetKind.WINDOWS_HOST:
            if self._windows_executor is None:
                raise LookupError("no Windows remediation executor is configured")
            return self._windows_executor
        raise LookupError("no remediation executor is configured for network devices")


class FabricIncidentHandler(IncidentRemediationHandler):
    """Handle phase-3 incident chat and visibility inside one client fabric."""

    def __init__(
        self,
        *,
        incident_context_reader: IncidentContextReader,
        approval_controller: ApprovalController,
        notifier: NotifierTransportAgent,
        orchestrator: CoreOrchestratorAgent,
        diagnostics_executor: DiagnosticsExecutor,
        remediation_executor: RemediationExecutor | None = None,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._approval_controller = approval_controller
        self._notifier = notifier
        self._orchestrator = orchestrator
        self._diagnostics_executor = diagnostics_executor
        self._remediation_executor = remediation_executor

    async def handle_incident_chat_request(
        self,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        """Dispatch one incident chat request through the core orchestrator."""

        toolset = _FabricIncidentToolset(
            incident_context_reader=self._incident_context_reader,
            approval_controller=self._approval_controller,
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

    async def execute_approved_remediation(
        self,
        *,
        approval_id: str,
        remediation: RemediationExecute,
    ) -> IncidentRemediationExecution:
        """Execute one approved remediation and collect verification evidence."""

        if self._remediation_executor is None:
            raise LookupError("no remediation executor is configured")
        incident = await self._incident_context_reader.get_incident(
            remediation.incident_id
        )
        if incident is None:
            raise LookupError("incident for remediation was not found")
        asset_ids = incident.asset_ids or [remediation.asset.asset_id]
        await self._notifier.append_activity(
            incident_id=remediation.incident_id,
            event_type="remediation.started",
            payload={
                "approval_id": approval_id,
                "action_type": remediation.action_type,
                "parameters": dict(remediation.parameters),
            },
            asset_id=remediation.asset.asset_id,
        )
        await self._notifier.persist_summary(
            incident_id=remediation.incident_id,
            summary=incident.summary,
            severity=incident.severity,
            state=IncidentState.REMEDIATING,
            recommended_actions=_active_remediation_actions(remediation),
            asset_ids=asset_ids,
        )
        recent_activity = (
            await self._incident_context_reader.list_activity_for_incident(
                remediation.incident_id
            )
        )
        remediation_result = await self._remediation_executor.execute_remediation(
            remediation,
            recent_activity=recent_activity[-5:],
        )
        await self._notifier.append_activity(
            incident_id=remediation.incident_id,
            event_type="remediation.executed",
            payload={
                "approval_id": approval_id,
                "action_type": remediation.action_type,
                "outcome": remediation_result.outcome,
                "audit_reference": remediation_result.audit_reference,
                "post_state": dict(remediation_result.post_state),
            },
            asset_id=remediation.asset.asset_id,
            occurred_at=remediation_result.completed_at,
        )
        verification_request = DiagnosticsCollect(
            request_id=str(uuid4()),
            incident_id=remediation.incident_id,
            client_id=remediation.client_id,
            fabric_id=remediation.fabric_id,
            asset=remediation.asset,
            diagnostic_profile=_verification_profile(remediation.asset.asset_kind),
            requested_actions=["verify-service-state"],
            timeout_seconds=60,
            read_only=True,
        )
        post_execution_activity = (
            await self._incident_context_reader.list_activity_for_incident(
                remediation.incident_id
            )
        )
        verification_execution = await self._diagnostics_executor.execute_diagnostics(
            verification_request,
            recent_activity=post_execution_activity[-5:],
        )
        await self._notifier.persist_evidence_bundle(
            bundle=verification_execution.evidence_bundle,
            client_id=remediation.client_id,
            fabric_id=remediation.fabric_id,
        )
        await self._notifier.append_activity(
            incident_id=remediation.incident_id,
            event_type="diagnostics.completed",
            payload={
                "approval_id": approval_id,
                "outcome": verification_execution.result.outcome,
                "evidence_bundle_id": verification_execution.result.evidence_bundle_id,
                "structured_results": verification_execution.result.structured_results,
                "verification": True,
            },
            asset_id=remediation.asset.asset_id,
            occurred_at=verification_execution.result.completed_at,
        )
        next_state = _target_state(remediation_result)
        await self._notifier.append_activity(
            incident_id=remediation.incident_id,
            event_type="remediation.verified",
            payload={
                "approval_id": approval_id,
                "incident_state": next_state.value,
                "evidence_bundle_id": verification_execution.evidence_bundle.evidence_bundle_id,
            },
            asset_id=remediation.asset.asset_id,
            occurred_at=verification_execution.result.completed_at,
        )
        await self._notifier.persist_summary(
            incident_id=remediation.incident_id,
            summary=verification_execution.evidence_bundle.summary,
            severity=incident.severity,
            state=next_state,
            recommended_actions=_terminal_recommendations(
                remediation_result,
                next_state,
            ),
            asset_ids=asset_ids,
        )
        return IncidentRemediationExecution(
            remediation_result=remediation_result,
            verification_result=verification_execution.result,
            verification_evidence_bundle=verification_execution.evidence_bundle,
            incident_state=next_state,
            operator_message=_operator_message(remediation_result, next_state),
        )


class _FabricIncidentToolset(CoreOrchestratorToolset):
    """Adapter that exposes read and write ports to the orchestrator."""

    def __init__(
        self,
        *,
        incident_context_reader: IncidentContextReader,
        approval_controller: ApprovalController,
        notifier: NotifierTransportAgent,
        diagnostics_executor: DiagnosticsExecutor,
        request: OperatorChatRequest,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._approval_controller = approval_controller
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

    async def request_approval(
        self,
        approval_request: ApprovalRequested,
    ) -> ApprovalRequested:
        approval = await self._approval_controller.request_approval(approval_request)
        return ApprovalRequested(
            approval_id=approval.approval_id,
            client_id=approval.client_id,
            fabric_id=approval.fabric_id,
            incident_id=approval.incident_id,
            action_kind=approval.action_kind,
            title=approval.title,
            requested_at=approval.requested_at,
            expires_at=approval.expires_at,
            requested_by_agent=approval.requested_by_agent,
            payload=dict(approval.payload),
            risk_summary=approval.risk_summary,
        )

    def _require_incident_id(self) -> str:
        incident_id = self._request.incident_id
        if incident_id is None:
            raise LookupError("incident chat requires an incident_id")
        return incident_id


def _verification_profile(asset_kind: AssetKind) -> str:
    if asset_kind in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
        return "host.services"
    return "uplink-health"


def _target_state(result: RemediationResult) -> IncidentState:
    if (
        result.outcome == "completed"
        and result.post_state.get("service_state") == "running"
    ):
        return IncidentState.RESOLVED
    return IncidentState.INVESTIGATING


def _active_remediation_actions(
    remediation: RemediationExecute,
) -> list[JSONObject]:
    service_name = remediation.parameters.get("service_name")
    details = (
        f"Executing {remediation.action_type} for {service_name}."
        if isinstance(service_name, str) and service_name
        else f"Executing {remediation.action_type}."
    )
    return [
        {
            "action_kind": "host.monitor",
            "title": "Await verification diagnostics",
            "details": details,
        }
    ]


def _terminal_recommendations(
    remediation_result: RemediationResult,
    incident_state: IncidentState,
) -> list[JSONObject]:
    if incident_state is IncidentState.RESOLVED:
        return [
            {
                "action_kind": "host.observe",
                "title": "Continue post-remediation monitoring",
                "details": (
                    "Verification evidence was captured after the approved host action."
                ),
            }
        ]
    return [
        {
            "action_kind": "host.investigate",
            "title": "Review the verification evidence",
            "details": (
                "The approved host action did not leave the target service in a "
                "running state."
            ),
        },
        {
            "action_kind": "host.audit",
            "title": "Review the executor audit reference",
            "details": (
                remediation_result.audit_reference or "No audit reference was recorded."
            ),
        },
    ]


def _operator_message(
    remediation_result: RemediationResult,
    incident_state: IncidentState,
) -> str:
    service_name = remediation_result.post_state.get("service_name")
    if isinstance(service_name, str) and service_name:
        subject = service_name
    else:
        subject = "the requested service"
    if incident_state is IncidentState.RESOLVED:
        return (
            f"Approved remediation completed for {subject}. "
            "Verification diagnostics were captured and the incident is now resolved."
        )
    return (
        f"Approved remediation completed for {subject}, but verification evidence "
        "shows the incident still needs investigation."
    )


__all__ = [
    "AssetKindDiagnosticsExecutor",
    "AssetKindRemediationExecutor",
    "DiagnosticsExecution",
    "DiagnosticsExecutor",
    "FabricIncidentHandler",
    "RemediationExecutor",
]
