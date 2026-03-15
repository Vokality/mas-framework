"""Contract validation tests for Phase 0 MSP foundations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from mas_msp_contracts import (
    AlertRaised,
    ApprovalDecision,
    ApprovalRequested,
    AssetKind,
    AssetRef,
    ChatScope,
    ChatTurnState,
    ConfigApplyRequested,
    ConfigApplyResult,
    ConfigApplyState,
    ConfigDesiredState,
    ConfigValidationResult,
    CredentialRef,
    DiagnosticsCollect,
    DiagnosticsResult,
    EvidenceBundle,
    HealthSnapshot,
    HealthState,
    IncidentChatContext,
    IncidentRecord,
    IncidentState,
    OperatorChatRequest,
    OperatorChatResponse,
    PortfolioEvent,
    RemediationExecute,
    RemediationResult,
    Severity,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
ASSET_ID = "33333333-3333-4333-8333-333333333333"
SNAPSHOT_ID = "44444444-4444-4444-8444-444444444444"
ALERT_ID = "55555555-5555-4555-8555-555555555555"
INCIDENT_ID = "66666666-6666-4666-8666-666666666666"
APPROVAL_ID = "77777777-7777-4777-8777-777777777777"
CHAT_SESSION_ID = "88888888-8888-4888-8888-888888888888"
CONFIG_RUN_ID = "99999999-9999-4999-8999-999999999999"
EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REQUEST_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TURN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
EVIDENCE_BUNDLE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _asset_payload() -> dict[str, object]:
    return {
        "asset_id": ASSET_ID,
        "client_id": CLIENT_ID,
        "fabric_id": FABRIC_ID,
        "asset_kind": AssetKind.NETWORK_DEVICE,
        "vendor": "Cisco",
        "model": "Catalyst 9300",
        "hostname": "edge-sw-01",
        "mgmt_address": "10.0.0.10",
        "site": "nyc-1",
        "tags": ["core", "switch"],
    }


def _timestamp() -> str:
    return "2026-03-15T14:30:00Z"


@pytest.mark.parametrize(
    ("model_cls", "payload"),
    [
        (
            AssetRef,
            _asset_payload(),
        ),
        (
            CredentialRef,
            {
                "credential_ref": "netops-snmp-ro",
                "provider_kind": "vault",
                "scope": {"client_id": CLIENT_ID},
                "purpose": "snmp_polling",
                "secret_path": "secret/clients/acme/snmp/ro",
            },
        ),
        (
            HealthSnapshot,
            {
                "snapshot_id": SNAPSHOT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "source_kind": "snmp_poll",
                "collected_at": _timestamp(),
                "health_state": HealthState.DEGRADED,
                "metrics": {"cpu_percent": 91.2, "memory_percent": 73.1},
                "findings": [{"code": "cpu_high", "summary": "CPU above threshold"}],
            },
        ),
        (
            AlertRaised,
            {
                "alert_id": ALERT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "source_kind": "syslog",
                "occurred_at": _timestamp(),
                "severity": Severity.MAJOR,
                "category": "interface",
                "title": "Uplink flap detected",
                "normalized_facts": {"interface": "Gi1/0/48", "flaps": 3},
                "raw_reference": {"stream": "syslog", "offset": "42"},
            },
        ),
        (
            DiagnosticsCollect,
            {
                "request_id": REQUEST_ID,
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "diagnostic_profile": "network.interfaces",
                "requested_actions": ["show_interfaces", "show_logs"],
                "timeout_seconds": 45,
                "read_only": True,
            },
        ),
        (
            DiagnosticsResult,
            {
                "request_id": REQUEST_ID,
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "completed_at": _timestamp(),
                "outcome": "succeeded",
                "evidence_bundle_id": EVIDENCE_BUNDLE_ID,
                "observations": [{"summary": "CRC errors on uplink"}],
                "structured_results": {"errors": 17},
            },
        ),
        (
            RemediationExecute,
            {
                "request_id": REQUEST_ID,
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "action_type": "service.restart",
                "parameters": {"service_name": "routing"},
                "approval_id": APPROVAL_ID,
            },
        ),
        (
            RemediationResult,
            {
                "request_id": REQUEST_ID,
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "completed_at": _timestamp(),
                "outcome": "succeeded",
                "audit_reference": "audit:42",
                "post_state": {"service_state": "running"},
            },
        ),
        (
            IncidentRecord,
            {
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "state": IncidentState.INVESTIGATING,
                "severity": Severity.CRITICAL,
                "summary": "Core uplink instability in NYC",
                "asset_ids": [ASSET_ID],
                "opened_at": _timestamp(),
                "updated_at": _timestamp(),
            },
        ),
        (
            EvidenceBundle,
            {
                "evidence_bundle_id": EVIDENCE_BUNDLE_ID,
                "incident_id": INCIDENT_ID,
                "asset_id": ASSET_ID,
                "collected_at": _timestamp(),
                "items": [{"kind": "cli_output", "label": "show interface"}],
                "summary": "Interface counters and log excerpts",
            },
        ),
        (
            PortfolioEvent,
            {
                "event_id": EVENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "event_type": "network.alert.raised",
                "subject_type": "alert",
                "subject_id": ALERT_ID,
                "occurred_at": _timestamp(),
                "payload_version": 1,
                "payload": {"alert_id": ALERT_ID, "asset_id": ASSET_ID},
            },
        ),
        (
            OperatorChatRequest,
            {
                "request_id": REQUEST_ID,
                "chat_session_id": CHAT_SESSION_ID,
                "turn_id": TURN_ID,
                "scope": ChatScope.INCIDENT,
                "actor_user_id": "operator-1",
                "allowed_client_ids": [CLIENT_ID],
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "incident_id": INCIDENT_ID,
                "asset_ids": [ASSET_ID],
                "message": "Collect more evidence for the uplink issue.",
            },
        ),
        (
            OperatorChatResponse,
            {
                "request_id": REQUEST_ID,
                "chat_session_id": CHAT_SESSION_ID,
                "turn_id": TURN_ID,
                "state": ChatTurnState.COMPLETED,
                "incident_id": INCIDENT_ID,
                "markdown_summary": "Diagnostics suggest an optics fault.",
                "evidence_bundle_ids": [EVIDENCE_BUNDLE_ID],
                "approval_id": APPROVAL_ID,
                "recommended_actions": [{"action_type": "replace_optic"}],
            },
        ),
        (
            IncidentChatContext,
            {
                "chat_session_id": CHAT_SESSION_ID,
                "incident_id": INCIDENT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset_ids": [ASSET_ID],
                "incident_state": IncidentState.INVESTIGATING,
                "recent_evidence_bundle_ids": [EVIDENCE_BUNDLE_ID],
            },
        ),
        (
            ApprovalRequested,
            {
                "approval_id": APPROVAL_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "incident_id": INCIDENT_ID,
                "action_kind": "network.remediation",
                "title": "Bounce primary uplink",
                "requested_at": _timestamp(),
                "expires_at": "2026-03-15T15:00:00Z",
                "requested_by_agent": "core-orchestrator",
                "payload": {"action_type": "interface.shutdown_no_shutdown"},
                "risk_summary": "Will briefly impact the WAN uplink.",
            },
        ),
        (
            ApprovalDecision,
            {
                "approval_id": APPROVAL_ID,
                "decided_by_user_id": "operator-1",
                "decision": "approve",
                "decided_at": _timestamp(),
                "reason": "Maintenance window is active.",
            },
        ),
        (
            ConfigDesiredState,
            {
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "desired_state_version": 3,
                "tenant_metadata": {"display_name": "Acme Corp"},
                "policy": {"default_mode": "deny"},
                "inventory_sources": [{"kind": "snmp", "site": "nyc-1"}],
                "notification_routes": [{"kind": "email", "target": "noc@example.com"}],
            },
        ),
        (
            ConfigValidationResult,
            {
                "config_apply_run_id": CONFIG_RUN_ID,
                "client_id": CLIENT_ID,
                "desired_state_version": 3,
                "status": "valid",
                "errors": [],
                "warnings": ["SNMP poll interval is aggressive."],
                "validated_at": _timestamp(),
            },
        ),
        (
            ConfigApplyRequested,
            {
                "config_apply_run_id": CONFIG_RUN_ID,
                "client_id": CLIENT_ID,
                "desired_state_version": 3,
                "requested_by_user_id": "admin-1",
                "requested_at": _timestamp(),
            },
        ),
        (
            ConfigApplyResult,
            {
                "config_apply_run_id": CONFIG_RUN_ID,
                "client_id": CLIENT_ID,
                "desired_state_version": 3,
                "status": ConfigApplyState.SUCCEEDED,
                "started_at": _timestamp(),
                "completed_at": "2026-03-15T14:31:00Z",
                "error_summary": None,
            },
        ),
    ],
)
def test_shared_contract_examples_validate(model_cls, payload) -> None:
    model = model_cls.model_validate(payload)
    dumped = model.model_dump(mode="json")

    assert dumped


def test_contract_serialization_preserves_uuid_strings_and_utc_encoding() -> None:
    snapshot = HealthSnapshot.model_validate(
        {
            "snapshot_id": SNAPSHOT_ID,
            "client_id": CLIENT_ID,
            "fabric_id": FABRIC_ID,
            "asset": _asset_payload(),
            "source_kind": "snmp_poll",
            "collected_at": datetime(2026, 3, 15, 14, 30, tzinfo=UTC),
            "health_state": HealthState.HEALTHY,
            "metrics": {"cpu_percent": 10.5},
            "findings": [],
        }
    )

    dumped = snapshot.model_dump(mode="json")

    assert dumped["snapshot_id"] == SNAPSHOT_ID
    assert dumped["collected_at"] == "2026-03-15T14:30:00Z"


def test_contracts_reject_non_canonical_uuid_strings() -> None:
    with pytest.raises(ValidationError, match="valid UUID string"):
        AssetRef.model_validate({**_asset_payload(), "asset_id": "not-a-uuid"})

    with pytest.raises(ValidationError, match="lowercase and hyphenated"):
        AssetRef.model_validate(
            {**_asset_payload(), "asset_id": ASSET_ID.replace("-", "")}
        )


def test_contracts_reject_non_utc_timestamps() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        HealthSnapshot.model_validate(
            {
                "snapshot_id": SNAPSHOT_ID,
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "asset": _asset_payload(),
                "source_kind": "snmp_poll",
                "collected_at": datetime(
                    2026,
                    3,
                    15,
                    15,
                    30,
                    tzinfo=timezone(timedelta(hours=1)),
                ),
                "health_state": HealthState.UNKNOWN,
                "metrics": {},
                "findings": [],
            }
        )


def test_incident_chat_request_requires_incident_context() -> None:
    with pytest.raises(ValidationError, match="incident_id"):
        OperatorChatRequest.model_validate(
            {
                "request_id": REQUEST_ID,
                "chat_session_id": CHAT_SESSION_ID,
                "turn_id": TURN_ID,
                "scope": ChatScope.INCIDENT,
                "actor_user_id": "operator-1",
                "allowed_client_ids": [CLIENT_ID],
                "client_id": CLIENT_ID,
                "fabric_id": FABRIC_ID,
                "message": "Collect more evidence.",
            }
        )
