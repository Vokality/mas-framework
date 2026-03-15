from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from mas_msp_contracts import (
    AlertRaised,
    AssetKind,
    AssetRef,
    ChatScope,
    ChatTurnState,
    IncidentRecord,
    IncidentState,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
    Severity,
)
from mas_msp_core import NotifierTransportAgent, OpsBridgeAgent


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
INCIDENT_ID = "33333333-3333-4333-8333-333333333333"
CHAT_SESSION_ID = "44444444-4444-4444-8444-444444444444"
TURN_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_ID = "66666666-6666-4666-8666-666666666666"
ASSET_ID = "77777777-7777-4777-8777-777777777777"
ALERT_ID = "88888888-8888-4888-8888-888888888888"


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


def _incident(*, severity: Severity = Severity.WARNING) -> IncidentRecord:
    now = datetime(2026, 3, 15, 14, 0, tzinfo=UTC)
    return IncidentRecord(
        incident_id=INCIDENT_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        state=IncidentState.INVESTIGATING,
        severity=severity,
        summary="Existing alert investigation",
        asset_ids=[ASSET_ID],
        opened_at=now,
        updated_at=now,
    )


def _request() -> OperatorChatRequest:
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
        message="Collect more evidence about the unstable uplink.",
    )


def _alert() -> AlertRaised:
    return AlertRaised(
        alert_id=ALERT_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset=_asset(),
        source_kind="syslog",
        occurred_at=datetime(2026, 3, 15, 14, 5, tzinfo=UTC),
        severity=Severity.MAJOR,
        category="interface",
        title="Primary uplink changed state to down",
        normalized_facts={"interface": "Gi1/0/48"},
    )


def _alert_event() -> PortfolioEvent:
    alert = _alert()
    return PortfolioEvent(
        event_id="99999999-9999-4999-8999-999999999999",
        client_id=alert.client_id,
        fabric_id=alert.fabric_id,
        event_type="network.alert.raised",
        subject_type="alert",
        subject_id=alert.alert_id,
        occurred_at=alert.occurred_at,
        payload_version=1,
        payload={"alert": alert.model_dump(mode="json")},
    )


@dataclass(slots=True)
class FakeIncidentChatHandler:
    requests: list[OperatorChatRequest] = field(default_factory=list)

    async def handle_incident_chat_request(
        self,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse:
        self.requests.append(request)
        return OperatorChatResponse(
            request_id=request.request_id,
            chat_session_id=request.chat_session_id,
            turn_id=request.turn_id,
            state=ChatTurnState.COMPLETED,
            incident_id=request.incident_id,
            markdown_summary="Collected the requested evidence.",
            evidence_bundle_ids=["99999999-9999-4999-8999-999999999999"],
            approval_id=None,
            recommended_actions=[],
        )


@dataclass(slots=True)
class FakeVisibilityAlertHandler:
    alerts: list[AlertRaised] = field(default_factory=list)

    async def handle_visibility_alert(self, alert: AlertRaised) -> IncidentRecord:
        self.alerts.append(alert)
        return _incident(severity=alert.severity)


@dataclass(slots=True)
class FakeIncidentContextReader:
    active_incident: IncidentRecord | None = None

    async def find_active_incident_for_asset(
        self,
        *,
        client_id: str,
        asset_id: str,
    ) -> IncidentRecord | None:
        assert client_id == CLIENT_ID
        assert asset_id == ASSET_ID
        return self.active_incident


@dataclass(slots=True)
class FakeNotifierTransport:
    visibility_calls: list[dict[str, object]] = field(default_factory=list)

    async def record_visibility_incident(
        self,
        *,
        incident_id: str | None,
        client_id: str,
        fabric_id: str,
        summary: str,
        severity: Severity,
        state: IncidentState,
        asset_ids: list[str],
        occurred_at: datetime,
        source: str,
        source_event_id: str,
    ) -> IncidentRecord:
        self.visibility_calls.append(
            {
                "incident_id": incident_id,
                "client_id": client_id,
                "fabric_id": fabric_id,
                "summary": summary,
                "severity": severity,
                "state": state,
                "asset_ids": asset_ids,
                "occurred_at": occurred_at,
                "source": source,
                "source_event_id": source_event_id,
            }
        )
        return IncidentRecord(
            incident_id=incident_id or INCIDENT_ID,
            client_id=client_id,
            fabric_id=fabric_id,
            state=state,
            severity=severity,
            summary=summary,
            asset_ids=asset_ids,
            opened_at=occurred_at,
            updated_at=occurred_at,
        )


@pytest.mark.asyncio
async def test_ops_bridge_routes_incident_chat_requests() -> None:
    handler = FakeIncidentChatHandler()
    bridge = OpsBridgeAgent(incident_chat_handler=handler)

    response = await bridge.dispatch_chat_request(request=_request())

    assert response.state is ChatTurnState.COMPLETED
    assert handler.requests == [_request()]


@pytest.mark.asyncio
async def test_ops_bridge_routes_visibility_alerts() -> None:
    handler = FakeVisibilityAlertHandler()
    bridge = OpsBridgeAgent(visibility_alert_handler=handler)

    incident = await bridge.dispatch_visibility_alert(event=_alert_event())

    assert incident.incident_id == INCIDENT_ID
    assert handler.alerts == [_alert()]


@pytest.mark.asyncio
async def test_notifier_transport_opens_new_incident_for_alert() -> None:
    transport = FakeNotifierTransport()
    agent = NotifierTransportAgent(
        incident_context_reader=FakeIncidentContextReader(),
        transport=transport,
        incident_id_factory=lambda: INCIDENT_ID,
    )

    incident = await agent.open_or_update_incident_from_alert(_alert())

    assert incident.state is IncidentState.OPEN
    assert transport.visibility_calls == [
        {
            "incident_id": INCIDENT_ID,
            "client_id": CLIENT_ID,
            "fabric_id": FABRIC_ID,
            "summary": "Primary uplink changed state to down",
            "severity": Severity.MAJOR,
            "state": IncidentState.OPEN,
            "asset_ids": [ASSET_ID],
            "occurred_at": datetime(2026, 3, 15, 14, 5, tzinfo=UTC),
            "source": "visibility",
            "source_event_id": "incident-alert:88888888-8888-4888-8888-888888888888",
        }
    ]


@pytest.mark.asyncio
async def test_notifier_transport_updates_active_incident_for_alert() -> None:
    transport = FakeNotifierTransport()
    agent = NotifierTransportAgent(
        incident_context_reader=FakeIncidentContextReader(active_incident=_incident()),
        transport=transport,
    )

    incident = await agent.open_or_update_incident_from_alert(_alert())

    assert incident.incident_id == INCIDENT_ID
    assert transport.visibility_calls == [
        {
            "incident_id": INCIDENT_ID,
            "client_id": CLIENT_ID,
            "fabric_id": FABRIC_ID,
            "summary": "Primary uplink changed state to down",
            "severity": Severity.MAJOR,
            "state": IncidentState.INVESTIGATING,
            "asset_ids": [ASSET_ID],
            "occurred_at": datetime(2026, 3, 15, 14, 5, tzinfo=UTC),
            "source": "visibility_update",
            "source_event_id": "incident-alert:88888888-8888-4888-8888-888888888888",
        }
    ]
