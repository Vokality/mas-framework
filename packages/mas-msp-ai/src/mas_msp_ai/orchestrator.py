"""Fabric-local incident orchestration for the Phase 3 cockpit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from mas_msp_contracts import (
    AlertRaised,
    AssetRef,
    ChatTurnState,
    DiagnosticsCollect,
    IncidentRecord,
    IncidentState,
    OperatorChatRequest,
    OperatorChatResponse,
    Severity,
)
from mas_msp_core import NotifierTransportAgent

from .deps import DiagnosticsPlannerDeps, SummaryComposerDeps, TriageDeps
from .outputs import DiagnosticsPlan, IncidentResponse, SummaryDraft, TriageDecision
from .summary import SummaryComposer
from .toolsets import CoreOrchestratorToolset


class CoreOrchestratorAgent:
    """Coordinate incident chat, diagnostics planning, and summary persistence."""

    def __init__(self, *, summary_composer: SummaryComposer | None = None) -> None:
        self._summary_composer = summary_composer or SummaryComposer()

    async def handle_chat_request(
        self,
        request: OperatorChatRequest,
        *,
        toolset: CoreOrchestratorToolset,
    ) -> OperatorChatResponse:
        """Process one incident-scoped operator chat request."""

        incident_record, recent_activity = await toolset.get_incident_context()
        asset_refs = await toolset.get_asset_context()
        recent_evidence = await toolset.get_recent_evidence()

        triage_deps = TriageDeps(
            client_id=request.client_id or incident_record.client_id,
            fabric_id=request.fabric_id or incident_record.fabric_id,
            incident_id=incident_record.incident_id,
            incident_record=incident_record,
            asset_refs=tuple(asset_refs),
            recent_activity=tuple(recent_activity),
            recent_evidence=tuple(recent_evidence),
            user_intent=request.message,
            allowed_actions=("read_only_diagnostics", "persist_summary"),
        )
        triage = await self._triage(triage_deps)

        evidence_bundle_ids = [bundle.evidence_bundle_id for bundle in recent_evidence]
        if triage.next_action == "collect_diagnostics":
            plan = await self._build_diagnostics_plan(
                DiagnosticsPlannerDeps(
                    incident_id=incident_record.incident_id,
                    asset_refs=tuple(asset_refs),
                    recent_evidence=tuple(recent_evidence),
                    available_profiles=tuple(triage.diagnostic_profiles),
                    allowed_actions=("read_only_diagnostics",),
                )
            )
            await toolset.append_activity(
                event_type="diagnostics.requested",
                payload={
                    "operator_message": plan.operator_message,
                    "steps": list(plan.steps),
                },
                asset_id=asset_refs[0].asset_id if asset_refs else None,
            )
            diagnostic_requests = self._build_diagnostic_requests(
                request=request,
                incident_id=incident_record.incident_id,
                asset_refs=asset_refs,
                profiles=triage.diagnostic_profiles,
            )
            results = await toolset.request_diagnostics(diagnostic_requests)
            for result in results:
                await toolset.append_activity(
                    event_type="diagnostics.completed",
                    payload={
                        "outcome": result.outcome,
                        "evidence_bundle_id": result.evidence_bundle_id,
                        "structured_results": result.structured_results,
                    },
                    asset_id=result.asset.asset_id,
                )
            recent_evidence = await toolset.get_recent_evidence()
            evidence_bundle_ids = [
                bundle.evidence_bundle_id for bundle in recent_evidence
            ]

        state = (
            IncidentState.INVESTIGATING
            if incident_record.state is IncidentState.OPEN
            or triage.next_action == "collect_diagnostics"
            else incident_record.state
        )
        summary = await self._summary_composer.compose(
            SummaryComposerDeps(
                incident_id=incident_record.incident_id,
                incident_record=incident_record.model_copy(update={"state": state}),
                asset_refs=tuple(asset_refs),
                recent_evidence=tuple(recent_evidence),
                recent_activity=tuple(recent_activity),
            )
        )
        persisted_incident = await toolset.persist_summary(
            summary=summary.headline,
            severity=triage.severity,
            state=state,
            recommended_actions=summary.recommended_actions,
            asset_ids=[asset.asset_id for asset in asset_refs],
        )
        response = await self._build_incident_response(
            request=request,
            summary=summary,
            evidence_bundle_ids=evidence_bundle_ids,
        )
        return OperatorChatResponse(
            request_id=request.request_id,
            chat_session_id=response.chat_session_id,
            turn_id=response.turn_id,
            state=response.state,
            incident_id=persisted_incident.incident_id,
            markdown_summary=response.markdown_summary,
            evidence_bundle_ids=response.evidence_bundle_ids,
            approval_id=None,
            recommended_actions=response.recommended_actions,
        )

    async def handle_visibility_alert(
        self,
        alert: AlertRaised,
        *,
        notifier: NotifierTransportAgent,
    ) -> IncidentRecord:
        """Ensure visibility alerts become durable incidents through the notifier."""

        return await notifier.open_or_update_incident_from_alert(alert)

    async def _triage(self, deps: TriageDeps) -> TriageDecision:
        needs_diagnostics = self._needs_diagnostics(deps)
        payload = TriageDecision(
            incident_id=deps.incident_id,
            summary=deps.incident_record.summary,
            severity=deps.incident_record.severity,
            next_action="collect_diagnostics" if needs_diagnostics else "summarize",
            diagnostic_profiles=(
                [self._default_profile(deps.user_intent)] if needs_diagnostics else []
            ),
            rationale=(
                "Additional read-only evidence is needed before recommending next steps."
                if needs_diagnostics
                else "Recent evidence is already sufficient to answer the operator."
            ),
        )
        agent = Agent(
            TestModel(custom_output_args=payload.model_dump(mode="json")),
            deps_type=TriageDeps,
            output_type=TriageDecision,
        )
        result = await agent.run("Triage the incident chat request.", deps=deps)
        return cast(TriageDecision, result.output)

    async def _build_diagnostics_plan(
        self, deps: DiagnosticsPlannerDeps
    ) -> DiagnosticsPlan:
        steps = [
            f"Run {profile} against {asset.hostname or asset.asset_id}"
            for asset in deps.asset_refs
            for profile in deps.available_profiles
        ]
        payload = DiagnosticsPlan(
            incident_id=deps.incident_id,
            steps=steps or ["Collect the default read-only diagnostics profile."],
            continue_in_background=True,
            operator_message="Collecting read-only diagnostics for the affected assets.",
        )
        agent = Agent(
            TestModel(custom_output_args=payload.model_dump(mode="json")),
            deps_type=DiagnosticsPlannerDeps,
            output_type=DiagnosticsPlan,
        )
        result = await agent.run("Plan read-only diagnostics.", deps=deps)
        return cast(DiagnosticsPlan, result.output)

    async def _build_incident_response(
        self,
        *,
        request: OperatorChatRequest,
        summary: SummaryDraft,
        evidence_bundle_ids: list[str],
    ) -> IncidentResponse:
        payload = IncidentResponse(
            chat_session_id=request.chat_session_id,
            turn_id=request.turn_id,
            state=ChatTurnState.COMPLETED,
            markdown_summary=summary.operator_summary,
            evidence_bundle_ids=evidence_bundle_ids,
            recommended_actions=summary.recommended_actions,
            approval_required=False,
        )
        agent = Agent(
            TestModel(custom_output_args=payload.model_dump(mode="json")),
            deps_type=SummaryComposerDeps,
            output_type=IncidentResponse,
        )
        result = await agent.run(
            "Build the final operator-facing incident response.",
            deps=SummaryComposerDeps(
                incident_id=summary.incident_id,
                incident_record=request_to_incident_record(request, summary.headline),
            ),
        )
        return cast(IncidentResponse, result.output)

    def _needs_diagnostics(self, deps: TriageDeps) -> bool:
        normalized = deps.user_intent.lower()
        if not deps.recent_evidence:
            return True
        keywords = ("collect", "run diagnostic", "run diagnostics", "investigate")
        return any(word in normalized for word in keywords)

    def _default_profile(self, user_intent: str) -> str:
        normalized = user_intent.lower()
        if "vpn" in normalized:
            return "vpn-health"
        return "uplink-health"

    def _build_diagnostic_requests(
        self,
        *,
        request: OperatorChatRequest,
        incident_id: str,
        asset_refs: list[AssetRef],
        profiles: list[str],
    ) -> list[DiagnosticsCollect]:
        profile = profiles[0] if profiles else "uplink-health"
        return [
            DiagnosticsCollect(
                request_id=str(uuid4()),
                incident_id=incident_id,
                client_id=request.client_id or asset.client_id,
                fabric_id=request.fabric_id or asset.fabric_id,
                asset=asset,
                diagnostic_profile=profile,
                requested_actions=[
                    "collect-interface-status",
                    "collect-health-snapshot",
                ],
                timeout_seconds=60,
                read_only=True,
            )
            for asset in asset_refs
        ]


def request_to_incident_record(
    request: OperatorChatRequest,
    summary: str,
) -> IncidentRecord:
    """Build a minimal incident record for response validation deps."""

    now = datetime.now(UTC)
    return IncidentRecord(
        incident_id=request.incident_id or str(uuid4()),
        client_id=request.client_id or str(uuid4()),
        fabric_id=request.fabric_id or str(uuid4()),
        state=IncidentState.INVESTIGATING,
        severity=Severity.MAJOR,
        summary=summary,
        asset_ids=list(request.asset_ids),
        opened_at=now,
        updated_at=now,
    )


__all__ = ["CoreOrchestratorAgent"]
