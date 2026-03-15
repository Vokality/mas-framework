"""Fabric-local incident orchestration for the Phase 3 cockpit."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import re
from typing import cast
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from mas_msp_contracts import (
    ApprovalRequested,
    AlertRaised,
    AssetKind,
    AssetRef,
    ChatTurnState,
    DiagnosticsCollect,
    IncidentRecord,
    IncidentState,
    OperatorChatRequest,
    OperatorChatResponse,
    RemediationExecute,
    Severity,
)
from mas_msp_core import NotifierTransportAgent

from .deps import DiagnosticsPlannerDeps, SummaryComposerDeps, TriageDeps
from .outputs import DiagnosticsPlan, IncidentResponse, SummaryDraft, TriageDecision
from .summary import SummaryComposer
from .toolsets import CoreOrchestratorToolset


class CoreOrchestratorAgent:
    """Coordinate incident chat, diagnostics planning, and summary persistence."""

    def __init__(
        self,
        *,
        summary_composer: SummaryComposer | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        remediation_request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._summary_composer = summary_composer or SummaryComposer()
        self._approval_id_factory = approval_id_factory or (lambda: str(uuid4()))
        self._remediation_request_id_factory = remediation_request_id_factory or (
            lambda: str(uuid4())
        )

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

        requested_write = self._requires_approval(request.message)
        approval_request = (
            self._build_remediation_approval(
                request=request,
                incident_id=incident_record.incident_id,
                asset_refs=asset_refs,
                summary=incident_record.summary,
            )
            if requested_write
            else None
        )
        requires_approval = approval_request is not None
        state = self._target_incident_state(
            incident_record=incident_record,
            collected_diagnostics=triage.next_action == "collect_diagnostics",
            requires_approval=requires_approval,
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
        if approval_request is not None:
            approval = await toolset.request_approval(approval_request)
            await toolset.append_activity(
                event_type="approval.requested",
                payload={
                    "approval_id": approval.approval_id,
                    "action_kind": approval.action_kind,
                    "title": approval.title,
                },
                asset_id=asset_refs[0].asset_id if asset_refs else None,
            )
            persisted_incident = await toolset.persist_summary(
                summary=summary.headline,
                severity=triage.severity,
                state=state,
                recommended_actions=summary.recommended_actions,
                asset_ids=[asset.asset_id for asset in asset_refs],
            )
            return OperatorChatResponse(
                request_id=request.request_id,
                chat_session_id=request.chat_session_id,
                turn_id=request.turn_id,
                state=ChatTurnState.WAITING_FOR_APPROVAL,
                incident_id=persisted_incident.incident_id,
                markdown_summary=(
                    "Approval is required before the requested remediation can run. "
                    "The incident will remain paused until an authorized operator "
                    "approves it."
                ),
                evidence_bundle_ids=evidence_bundle_ids,
                approval_id=approval.approval_id,
                recommended_actions=summary.recommended_actions,
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
            markdown_summary=(
                f"{response.markdown_summary}\n\n"
                "Only typed host service.start, service.stop, and "
                "service.restart actions with an explicit service name are "
                "supported in v1."
                if requested_write and approval_request is None
                else response.markdown_summary
            ),
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
                [self._default_profile(deps.user_intent, deps.asset_refs)]
                if needs_diagnostics
                else []
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

    def _requires_approval(self, user_intent: str) -> bool:
        normalized = user_intent.lower()
        approval_keywords = (
            "apply",
            "block",
            "bounce",
            "clear",
            "cycle",
            "disable",
            "enable",
            "fail over",
            "failover",
            "reboot",
            "reset",
            "restart",
            "shut",
            "shutdown",
            "unblock",
        )
        return any(keyword in normalized for keyword in approval_keywords)

    def _target_incident_state(
        self,
        *,
        incident_record: IncidentRecord,
        collected_diagnostics: bool,
        requires_approval: bool,
    ) -> IncidentState:
        if requires_approval:
            return IncidentState.AWAITING_APPROVAL
        if incident_record.state is IncidentState.OPEN or collected_diagnostics:
            return IncidentState.INVESTIGATING
        return incident_record.state

    def _default_profile(
        self, user_intent: str, asset_refs: tuple[AssetRef, ...]
    ) -> str:
        normalized = user_intent.lower()
        primary_asset = asset_refs[0] if asset_refs else None
        if primary_asset is not None and primary_asset.asset_kind in {
            AssetKind.LINUX_HOST,
            AssetKind.WINDOWS_HOST,
        }:
            if "service" in normalized or "restart" in normalized:
                return "host.services"
            if "disk" in normalized:
                return "host.disk"
            if "log" in normalized:
                return (
                    "host.logs"
                    if primary_asset.asset_kind is AssetKind.LINUX_HOST
                    else "host.event_logs"
                )
            if "performance" in normalized:
                return "host.performance"
            return "host.summary"
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
        return [
            DiagnosticsCollect(
                request_id=str(uuid4()),
                incident_id=incident_id,
                client_id=request.client_id or asset.client_id,
                fabric_id=request.fabric_id or asset.fabric_id,
                asset=asset,
                diagnostic_profile=self._profile_for_asset(
                    asset=asset,
                    requested_profile=profiles[0] if profiles else None,
                    user_intent=request.message,
                ),
                requested_actions=self._requested_actions_for_asset(
                    asset=asset,
                    diagnostic_profile=self._profile_for_asset(
                        asset=asset,
                        requested_profile=profiles[0] if profiles else None,
                        user_intent=request.message,
                    ),
                ),
                timeout_seconds=60,
                read_only=True,
            )
            for asset in asset_refs
        ]

    def _build_remediation_approval(
        self,
        *,
        request: OperatorChatRequest,
        incident_id: str,
        asset_refs: list[AssetRef],
        summary: str,
    ) -> ApprovalRequested | None:
        now = datetime.now(UTC)
        if not asset_refs:
            raise LookupError("incident remediation requires at least one asset")
        primary_asset = asset_refs[0]
        if primary_asset.asset_kind in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
            host_action = self._parse_host_service_action(request.message)
            if host_action is None:
                return None
            action_type, service_name = host_action
            approval_id = self._approval_id_factory()
            remediation = RemediationExecute(
                request_id=self._remediation_request_id_factory(),
                incident_id=incident_id,
                client_id=request.client_id or primary_asset.client_id,
                fabric_id=request.fabric_id or primary_asset.fabric_id,
                asset=primary_asset,
                action_type=action_type,
                parameters={"service_name": service_name},
                approval_id=approval_id,
            )
            verb = action_type.removeprefix("service.")
            return ApprovalRequested(
                approval_id=approval_id,
                client_id=request.client_id or primary_asset.client_id,
                fabric_id=request.fabric_id or primary_asset.fabric_id,
                incident_id=incident_id,
                action_kind="host.remediation",
                title=(
                    f"{verb.capitalize()} {service_name} on "
                    f"{primary_asset.hostname or primary_asset.asset_id}"
                ),
                requested_at=now,
                expires_at=now.replace(microsecond=0) + timedelta(hours=1),
                requested_by_agent="core-orchestrator",
                payload={
                    "action_scope": "incident_remediation",
                    "chat_session_id": request.chat_session_id,
                    "turn_id": request.turn_id,
                    "incident_id": incident_id,
                    "asset_ids": [asset.asset_id for asset in asset_refs],
                    "requested_action": request.message,
                    "remediation_execute": remediation.model_dump(mode="json"),
                },
                risk_summary=(
                    "Executing this host service-control action mutates the "
                    "managed system and immediately triggers verification "
                    "diagnostics before the incident can resolve."
                ),
            )
        approval_id = self._approval_id_factory()
        remediation = RemediationExecute(
            request_id=self._remediation_request_id_factory(),
            incident_id=incident_id,
            client_id=request.client_id or primary_asset.client_id,
            fabric_id=request.fabric_id or primary_asset.fabric_id,
            asset=primary_asset,
            action_type="network.remediation",
            parameters={"operator_message": request.message, "summary": summary},
            approval_id=approval_id,
        )
        return ApprovalRequested(
            approval_id=approval_id,
            client_id=request.client_id or primary_asset.client_id,
            fabric_id=request.fabric_id or primary_asset.fabric_id,
            incident_id=incident_id,
            action_kind="network.remediation",
            title=f"Execute remediation for {primary_asset.hostname or primary_asset.asset_id}",
            requested_at=now,
            expires_at=now.replace(microsecond=0) + timedelta(hours=1),
            requested_by_agent="core-orchestrator",
            payload={
                "action_scope": "incident_remediation",
                "chat_session_id": request.chat_session_id,
                "turn_id": request.turn_id,
                "incident_id": incident_id,
                "asset_ids": [asset.asset_id for asset in asset_refs],
                "requested_action": request.message,
                "remediation_execute": remediation.model_dump(mode="json"),
            },
            risk_summary=(
                "Executing this remediation mutates managed infrastructure and may "
                "briefly impact service while recovery proceeds."
            ),
        )

    def _profile_for_asset(
        self,
        *,
        asset: AssetRef,
        requested_profile: str | None,
        user_intent: str,
    ) -> str:
        if asset.asset_kind in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}:
            if requested_profile and requested_profile.startswith("host."):
                return requested_profile
            return self._default_profile(user_intent, (asset,))
        if requested_profile and not requested_profile.startswith("host."):
            return requested_profile
        return "uplink-health"

    def _requested_actions_for_asset(
        self,
        *,
        asset: AssetRef,
        diagnostic_profile: str,
    ) -> list[str]:
        if asset.asset_kind is AssetKind.LINUX_HOST:
            if diagnostic_profile == "host.services":
                return ["collect-service-status", "collect-systemd-health"]
            if diagnostic_profile == "host.disk":
                return ["collect-disk-usage", "collect-filesystem-health"]
            if diagnostic_profile == "host.logs":
                return ["collect-journal-errors", "collect-service-failures"]
            return ["collect-host-summary", "collect-service-status"]
        if asset.asset_kind is AssetKind.WINDOWS_HOST:
            if diagnostic_profile == "host.services":
                return ["collect-service-status", "collect-scm-events"]
            if diagnostic_profile == "host.event_logs":
                return ["collect-event-log-errors", "collect-service-events"]
            if diagnostic_profile == "host.performance":
                return ["collect-performance-counters", "collect-service-status"]
            return ["collect-host-summary", "collect-service-status"]
        return ["collect-interface-status", "collect-health-snapshot"]

    def _parse_host_service_action(
        self,
        user_intent: str,
    ) -> tuple[str, str] | None:
        normalized = " ".join(user_intent.lower().split())
        for action_type, verbs in (
            ("service.restart", ("restart", "bounce", "cycle")),
            ("service.start", ("start", "enable")),
            ("service.stop", ("stop", "disable")),
        ):
            for verb in verbs:
                pattern = rf"{verb}\s+(?:the\s+)?([a-z0-9_.-]+)(?:\s+service)?"
                match = re.search(pattern, normalized)
                if match is None:
                    continue
                service_name = match.group(1).strip(" .,!?:;")
                if service_name in {"host", "server", "system"}:
                    continue
                return action_type, service_name
        return None


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
