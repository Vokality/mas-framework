from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from mas_msp_contracts import (
    AssetKind,
    CredentialRef,
    DiagnosticsCollect,
    RemediationExecute,
)
from mas_msp_hosts import (
    HostServiceRegistry,
    WindowsDiagnosticsAgent,
    WindowsEventIngestAgent,
    WindowsEventRecord,
    WindowsExecutorAgent,
    WindowsPollingAgent,
    WindowsPollObservation,
    WindowsPollingTarget,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
INCIDENT_ID = "33333333-3333-4333-8333-333333333333"
ASSET_ID = "44444444-4444-4444-8444-444444444444"


@dataclass(slots=True)
class FakeWindowsPoller:
    async def poll(self, target: WindowsPollingTarget) -> WindowsPollObservation:
        del target
        return WindowsPollObservation(
            collected_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
            metrics={"cpu_percent": 28, "memory_percent": 49, "disk_percent": 58},
            services=[
                {"service_name": "Spooler", "service_state": "stopped"},
                {"service_name": "W32Time", "service_state": "running"},
            ],
            findings=[],
        )


def _asset():
    from mas_msp_contracts import AssetRef

    return AssetRef(
        asset_id=ASSET_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.WINDOWS_HOST,
        vendor="Windows",
        model="Windows Server 2022",
        hostname="print-01",
        mgmt_address="10.0.2.15",
        site="nyc-1",
        tags=["windows"],
    )


def test_windows_event_ingest_normalizes_service_alert() -> None:
    agent = WindowsEventIngestAgent()

    alert = agent.normalize_wef_event(
        WindowsEventRecord(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            occurred_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
            message="service entered the stopped state",
            level="warning",
            hostname="print-01",
            mgmt_address="10.0.2.15",
            edition="Windows Server 2022",
            site="nyc-1",
            service_name="Spooler",
            tags=["production"],
        )
    )

    assert alert.asset.asset_kind is AssetKind.WINDOWS_HOST
    assert alert.category == "service"
    assert alert.normalized_facts["service_name"] == "Spooler"


def test_windows_polling_normalizes_host_snapshot() -> None:
    agent = WindowsPollingAgent(poller=FakeWindowsPoller())

    snapshot = agent.normalize_poll(
        WindowsPollingTarget(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            credential_ref=CredentialRef(
                credential_ref="cred-2",
                provider_kind="vault",
                purpose="windows-poll",
                secret_path="secret/windows/print-01",
            ),
            hostname="print-01",
            mgmt_address="10.0.2.15",
            edition="Windows Server 2022",
            site="nyc-1",
            tags=["production"],
        ),
        WindowsPollObservation(
            collected_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
            metrics={"cpu_percent": 28, "memory_percent": 49, "disk_percent": 58},
            services=[
                {"service_name": "Spooler", "service_state": "stopped"},
                {"service_name": "W32Time", "service_state": "running"},
            ],
            findings=[],
        ),
    )

    assert snapshot.asset.asset_kind is AssetKind.WINDOWS_HOST
    assert snapshot.health_state.value == "critical"
    assert snapshot.metrics["services"][0]["service_name"] == "Spooler"


@pytest.mark.asyncio
async def test_windows_diagnostics_and_executor_share_service_state() -> None:
    service_registry = HostServiceRegistry()
    diagnostics = WindowsDiagnosticsAgent(service_registry=service_registry)
    executor = WindowsExecutorAgent(service_registry=service_registry)
    request = DiagnosticsCollect(
        request_id="55555555-5555-4555-8555-555555555556",
        incident_id=INCIDENT_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset=_asset(),
        diagnostic_profile="host.services",
        requested_actions=["collect-service-status"],
        timeout_seconds=60,
        read_only=True,
    )

    before = await diagnostics.execute_diagnostics(
        request,
        recent_activity=[{"payload": {"service_name": "Spooler"}}],
    )
    await executor.execute_remediation(
        RemediationExecute(
            request_id="66666666-6666-4666-8666-666666666667",
            incident_id=INCIDENT_ID,
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            asset=_asset(),
            action_type="service.start",
            parameters={"service_name": "Spooler"},
            approval_id="77777777-7777-4777-8777-777777777778",
        )
    )
    after = await diagnostics.execute_diagnostics(
        request,
        recent_activity=[{"payload": {"service_name": "Spooler"}}],
    )

    assert before.evidence_bundle.items[1]["services"][0]["service_state"] == "stopped"
    assert after.evidence_bundle.items[1]["services"][0]["service_state"] == "running"
