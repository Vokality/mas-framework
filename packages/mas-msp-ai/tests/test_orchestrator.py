from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from mas_msp_ai import CoreOrchestratorAgent, SummaryComposer
from mas_msp_ai.toolsets import CoreOrchestratorToolset
from mas_msp_contracts import (
    ApprovalRequested,
    AssetKind,
    AssetRef,
    ChatScope,
    ChatTurnState,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    OperatorChatRequest,
    Severity,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
INCIDENT_ID = "33333333-3333-4333-8333-333333333333"
CHAT_SESSION_ID = "44444444-4444-4444-8444-444444444444"
TURN_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_ID = "66666666-6666-4666-8666-666666666666"
ASSET_ID = "77777777-7777-4777-8777-777777777777"
EVIDENCE_ID = "88888888-8888-4888-8888-888888888888"
APPROVAL_ID = "99999999-9999-4999-8999-999999999999"


def _asset() -> AssetRef:
    return AssetRef(
        asset_id=ASSET_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.NETWORK_DEVICE,
        vendor="Cisco",
        model="Catalyst 9300",
        hostname="edge-sw-01",
        mgmt_address="10.0.0.10",
        site="nyc-1",
        tags=["core"],
    )


def _incident() -> IncidentRecord:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    return IncidentRecord(
        incident_id=INCIDENT_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        state=IncidentState.OPEN,
        severity=Severity.MAJOR,
        summary="Primary uplink is unstable",
        asset_ids=[ASSET_ID],
        opened_at=now,
        updated_at=now,
    )


def _request(message: str) -> OperatorChatRequest:
    return OperatorChatRequest(
        request_id=REQUEST_ID,
        chat_session_id=CHAT_SESSION_ID,
        turn_id=TURN_ID,
        scope=ChatScope.INCIDENT,
        actor_user_id="operator-1",
        allowed_client_ids=[CLIENT_ID],
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        incident_id=INCIDENT_ID,
        asset_ids=[ASSET_ID],
        message=message,
    )


@dataclass(slots=True)
class FakeToolset(CoreOrchestratorToolset):
    incident: IncidentRecord = field(default_factory=_incident)
    asset_refs: list[AssetRef] = field(default_factory=lambda: [_asset()])
    recent_activity: list[dict[str, object]] = field(default_factory=list)
    recent_evidence: list[EvidenceBundle] = field(default_factory=list)
    requested_diagnostics: list[DiagnosticsCollect] = field(default_factory=list)
    appended_activity: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    persisted_summaries: list[tuple[str, list[dict[str, object]]]] = field(
        default_factory=list
    )
    approvals: list[ApprovalRequested] = field(default_factory=list)

    async def get_incident_context(
        self,
    ) -> tuple[IncidentRecord, list[dict[str, object]]]:
        return self.incident, list(self.recent_activity)

    async def get_asset_context(self) -> list[AssetRef]:
        return list(self.asset_refs)

    async def get_recent_evidence(self) -> list[EvidenceBundle]:
        return list(self.recent_evidence)

    async def request_diagnostics(
        self, requests: list[DiagnosticsCollect]
    ) -> list[DiagnosticsResult]:
        self.requested_diagnostics.extend(requests)
        result = DiagnosticsResult(
            request_id="99999999-9999-4999-8999-999999999999",
            incident_id=INCIDENT_ID,
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            asset=_asset(),
            completed_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
            outcome="completed",
            evidence_bundle_id=EVIDENCE_ID,
            observations=[{"kind": "diagnostic", "status": "captured"}],
            structured_results={"read_only": True, "profile": "uplink-health"},
        )
        self.recent_evidence.append(
            EvidenceBundle(
                evidence_bundle_id=EVIDENCE_ID,
                incident_id=INCIDENT_ID,
                asset_id=ASSET_ID,
                collected_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
                items=[
                    {
                        "kind": "interface_status",
                        "interface": "Gi1/0/48",
                        "state": "down",
                    }
                ],
                summary="Diagnostics confirmed the primary uplink is down.",
            )
        )
        return [result]

    async def append_activity(
        self,
        *,
        event_type: str,
        payload: dict[str, object],
        asset_id: str | None,
    ) -> None:
        del asset_id
        self.appended_activity.append((event_type, payload))

    async def persist_summary(
        self,
        *,
        summary: str,
        severity: Severity,
        state: IncidentState,
        recommended_actions: list[dict[str, object]],
        asset_ids: list[str],
    ) -> IncidentRecord:
        self.persisted_summaries.append((summary, recommended_actions))
        self.incident = self.incident.model_copy(
            update={
                "summary": summary,
                "severity": severity,
                "state": state,
                "asset_ids": asset_ids,
                "updated_at": datetime(2026, 3, 15, 12, 6, tzinfo=UTC),
            }
        )
        return self.incident

    async def request_approval(
        self,
        approval_request: ApprovalRequested,
    ) -> ApprovalRequested:
        self.approvals.append(approval_request)
        return approval_request


@pytest.mark.asyncio
async def test_orchestrator_requests_diagnostics_when_operator_asks_for_evidence() -> (
    None
):
    toolset = FakeToolset()
    orchestrator = CoreOrchestratorAgent(
        summary_composer=SummaryComposer(),
        approval_id_factory=lambda: APPROVAL_ID,
    )

    response = await orchestrator.handle_chat_request(
        _request("Collect more evidence about the unstable uplink."),
        toolset=toolset,
    )

    assert response.state is ChatTurnState.COMPLETED
    assert response.incident_id == INCIDENT_ID
    assert response.evidence_bundle_ids == [EVIDENCE_ID]
    assert toolset.requested_diagnostics
    assert toolset.persisted_summaries
    assert any(
        activity[0] == "diagnostics.completed" for activity in toolset.appended_activity
    )


@pytest.mark.asyncio
async def test_orchestrator_uses_existing_evidence_without_new_diagnostics() -> None:
    toolset = FakeToolset(
        recent_evidence=[
            EvidenceBundle(
                evidence_bundle_id=EVIDENCE_ID,
                incident_id=INCIDENT_ID,
                asset_id=ASSET_ID,
                collected_at=datetime(2026, 3, 15, 12, 1, tzinfo=UTC),
                items=[{"kind": "snapshot", "health_state": "degraded"}],
                summary="Recent evidence already shows uplink degradation.",
            )
        ]
    )
    orchestrator = CoreOrchestratorAgent(
        summary_composer=SummaryComposer(),
        approval_id_factory=lambda: APPROVAL_ID,
    )

    response = await orchestrator.handle_chat_request(
        _request("Summarize the current evidence for the incident."),
        toolset=toolset,
    )

    assert response.state is ChatTurnState.COMPLETED
    assert response.evidence_bundle_ids == [EVIDENCE_ID]
    assert toolset.requested_diagnostics == []
    assert toolset.persisted_summaries


@pytest.mark.asyncio
async def test_orchestrator_pauses_turn_and_requests_approval_for_write_actions() -> (
    None
):
    toolset = FakeToolset(
        recent_evidence=[
            EvidenceBundle(
                evidence_bundle_id=EVIDENCE_ID,
                incident_id=INCIDENT_ID,
                asset_id=ASSET_ID,
                collected_at=datetime(2026, 3, 15, 12, 1, tzinfo=UTC),
                items=[{"kind": "snapshot", "health_state": "degraded"}],
                summary="Recent evidence already shows uplink degradation.",
            )
        ]
    )
    orchestrator = CoreOrchestratorAgent(
        summary_composer=SummaryComposer(),
        approval_id_factory=lambda: APPROVAL_ID,
    )

    response = await orchestrator.handle_chat_request(
        _request("Bounce the uplink to restore service."),
        toolset=toolset,
    )

    assert response.state is ChatTurnState.WAITING_FOR_APPROVAL
    assert response.approval_id == APPROVAL_ID
    assert toolset.requested_diagnostics == []
    assert toolset.approvals[0].action_kind == "network.remediation"
    assert toolset.approvals[0].incident_id == INCIDENT_ID
    assert toolset.incident.state is IncidentState.AWAITING_APPROVAL
