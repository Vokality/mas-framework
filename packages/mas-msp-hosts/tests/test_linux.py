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
    LinuxDiagnosticsAgent,
    LinuxEventIngestAgent,
    LinuxExecutorAgent,
    LinuxJournalEvent,
    LinuxPollingAgent,
    LinuxPollObservation,
    LinuxPollingTarget,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"
INCIDENT_ID = "33333333-3333-4333-8333-333333333333"
ASSET_ID = "44444444-4444-4444-8444-444444444444"


@dataclass(slots=True)
class FakeLinuxPoller:
    async def poll(self, target: LinuxPollingTarget) -> LinuxPollObservation:
        del target
        return LinuxPollObservation(
            collected_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
            metrics={"cpu_percent": 32, "memory_percent": 54, "disk_percent": 67},
            services=[
                {"service_name": "nginx", "service_state": "stopped"},
                {"service_name": "sshd", "service_state": "running"},
            ],
            findings=[],
        )


def _asset():
    from mas_msp_contracts import AssetRef

    return AssetRef(
        asset_id=ASSET_ID,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.LINUX_HOST,
        vendor="Linux",
        model="Ubuntu 24.04",
        hostname="web-01",
        mgmt_address="10.0.1.10",
        site="nyc-1",
        tags=["linux"],
    )


def test_linux_event_ingest_normalizes_service_alert() -> None:
    agent = LinuxEventIngestAgent()

    alert = agent.normalize_journal_event(
        LinuxJournalEvent(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            occurred_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
            message="entered failed state",
            level="error",
            hostname="web-01",
            mgmt_address="10.0.1.10",
            distribution="Ubuntu 24.04",
            site="nyc-1",
            service_name="nginx",
            tags=["production"],
        )
    )

    assert alert.asset.asset_kind is AssetKind.LINUX_HOST
    assert alert.category == "service"
    assert alert.normalized_facts["service_name"] == "nginx"


def test_linux_polling_normalizes_host_snapshot() -> None:
    agent = LinuxPollingAgent(poller=FakeLinuxPoller())

    snapshot = agent.normalize_poll(
        LinuxPollingTarget(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            credential_ref=CredentialRef(
                credential_ref="cred-1",
                provider_kind="vault",
                purpose="linux-poll",
                secret_path="secret/linux/web-01",
            ),
            hostname="web-01",
            mgmt_address="10.0.1.10",
            distribution="Ubuntu 24.04",
            site="nyc-1",
            tags=["production"],
        ),
        LinuxPollObservation(
            collected_at=datetime(2026, 3, 15, 12, 5, tzinfo=UTC),
            metrics={"cpu_percent": 32, "memory_percent": 54, "disk_percent": 67},
            services=[
                {"service_name": "nginx", "service_state": "stopped"},
                {"service_name": "sshd", "service_state": "running"},
            ],
            findings=[],
        ),
    )

    assert snapshot.asset.asset_kind is AssetKind.LINUX_HOST
    assert snapshot.health_state.value == "critical"
    assert snapshot.metrics["services"][0]["service_name"] == "nginx"


@pytest.mark.asyncio
async def test_linux_diagnostics_and_executor_share_service_state() -> None:
    service_registry = HostServiceRegistry()
    diagnostics = LinuxDiagnosticsAgent(service_registry=service_registry)
    executor = LinuxExecutorAgent(service_registry=service_registry)
    request = DiagnosticsCollect(
        request_id="55555555-5555-4555-8555-555555555555",
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
        recent_activity=[{"payload": {"service_name": "nginx"}}],
    )
    await executor.execute_remediation(
        RemediationExecute(
            request_id="66666666-6666-4666-8666-666666666666",
            incident_id=INCIDENT_ID,
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            asset=_asset(),
            action_type="service.restart",
            parameters={"service_name": "nginx"},
            approval_id="77777777-7777-4777-8777-777777777777",
        )
    )
    after = await diagnostics.execute_diagnostics(
        request,
        recent_activity=[{"payload": {"service_name": "nginx"}}],
    )

    assert before.evidence_bundle.items[1]["services"][0]["service_state"] == "failed"
    assert after.evidence_bundle.items[1]["services"][0]["service_state"] == "running"
