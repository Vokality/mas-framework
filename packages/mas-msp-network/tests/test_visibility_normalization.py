from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from mas_msp_contracts import CredentialRef, HealthState, Severity
from mas_msp_network import (
    NetworkEventIngestAgent,
    NetworkPollingAgent,
    SnmpPollObservation,
    SnmpTrapEvent,
    SnmpV3Target,
    SyslogEvent,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"


def _credential_ref() -> CredentialRef:
    return CredentialRef(
        credential_ref="snmp-ro",
        provider_kind="vault",
        scope={"client_id": CLIENT_ID},
        purpose="snmp_polling",
        secret_path="secret/net/snmp/ro",
    )


def test_cisco_syslog_normalizes_into_alert() -> None:
    agent = NetworkEventIngestAgent()
    alert = agent.normalize_syslog(
        SyslogEvent(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="Cisco",
            message="%LINK-3-UPDOWN: Interface Gi1/0/48, changed state to down",
            occurred_at=datetime(2026, 3, 15, 14, 30, tzinfo=UTC),
            hostname="edge-sw-01",
            mgmt_address="10.0.0.10",
            model="Catalyst 9300",
            serial="FTX1234ABC",
            tags=["core"],
        )
    )

    assert alert.source_kind == "syslog"
    assert alert.severity is Severity.MAJOR
    assert alert.asset.vendor == "Cisco"
    assert alert.normalized_facts["interface"] == "Gi1/0/48"
    assert alert.normalized_facts["serial"] == "FTX1234ABC"


def test_fortigate_syslog_normalizes_into_alert() -> None:
    agent = NetworkEventIngestAgent()
    alert = agent.normalize_syslog(
        SyslogEvent(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="FortiGate",
            message=(
                'date=2026-03-15 time=14:30:00 devname="fw-01" '
                'devid="FG100FTK12345678" level="alert" type="event" '
                'subtype="system" msg="HA status changed"'
            ),
            occurred_at=datetime(2026, 3, 15, 14, 30, tzinfo=UTC),
            mgmt_address="10.10.0.1",
            model="FortiGate 100F",
            tags=["edge"],
        )
    )

    assert alert.severity is Severity.CRITICAL
    assert alert.category == "system"
    assert alert.asset.hostname == "fw-01"
    assert alert.normalized_facts["devid"] == "FG100FTK12345678"


def test_cisco_trap_normalizes_into_alert() -> None:
    agent = NetworkEventIngestAgent()
    alert = agent.normalize_snmp_trap(
        SnmpTrapEvent(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="Cisco",
            trap_oid="1.3.6.1.4.1.9.9.41.2.0.1",
            occurred_at=datetime(2026, 3, 15, 14, 32, tzinfo=UTC),
            hostname="edge-sw-01",
            mgmt_address="10.0.0.10",
            serial="FTX1234ABC",
            varbinds={
                "severity": 2,
                "category": "power",
                "message": "Power supply failure detected",
            },
        )
    )

    assert alert.source_kind == "snmp_trap"
    assert alert.severity is Severity.MAJOR
    assert alert.category == "power"
    assert alert.normalized_facts["serial"] == "FTX1234ABC"


def test_fortigate_trap_normalizes_into_alert() -> None:
    agent = NetworkEventIngestAgent()
    alert = agent.normalize_snmp_trap(
        SnmpTrapEvent(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="Fortinet",
            trap_oid="1.3.6.1.4.1.12356.101.4.2.0.7",
            occurred_at=datetime(2026, 3, 15, 14, 33, tzinfo=UTC),
            hostname="fw-01",
            mgmt_address="10.10.0.1",
            varbinds={"level": "warning", "subtype": "vpn", "msg": "IPsec tunnel down"},
        )
    )

    assert alert.severity is Severity.WARNING
    assert alert.category == "vpn"
    assert alert.title == "IPsec tunnel down"


@pytest.mark.asyncio
async def test_polling_agent_normalizes_and_forwards_snapshot() -> None:
    poller = AsyncMock()
    poller.poll.return_value = SnmpPollObservation(
        collected_at=datetime(2026, 3, 15, 14, 45, tzinfo=UTC),
        metrics={
            "cpu_percent": 91,
            "memory_percent": 77,
            "session_utilization_percent": 97,
        },
        findings=[],
    )
    agent = NetworkPollingAgent(poller=poller)
    agent.send = AsyncMock()

    snapshot = await agent.poll_target(
        SnmpV3Target(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="FortiGate",
            credential_ref=_credential_ref(),
            hostname="fw-01",
            mgmt_address="10.10.0.1",
            model="FortiGate 100F",
            serial="FG100FTK12345678",
            tags=["edge"],
        )
    )

    assert snapshot.source_kind == "snmp_poll"
    assert snapshot.health_state is HealthState.CRITICAL
    assert snapshot.metrics["serial"] == "FG100FTK12345678"
    agent.send.assert_awaited_once()


def test_cisco_poll_normalizes_into_health_snapshot() -> None:
    agent = NetworkPollingAgent(poller=AsyncMock())
    snapshot = agent.normalize_poll(
        SnmpV3Target(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            vendor="Cisco",
            credential_ref=_credential_ref(),
            hostname="edge-sw-01",
            mgmt_address="10.0.0.10",
            model="Catalyst 9300",
            tags=["core"],
        ),
        SnmpPollObservation(
            collected_at=datetime(2026, 3, 15, 14, 45, tzinfo=UTC),
            metrics={
                "cpu_percent": 88,
                "memory_percent": 62,
                "interface_error_count": 4,
            },
            findings=[],
        ),
    )

    assert snapshot.health_state is HealthState.DEGRADED
    assert any(
        finding["metric"] == "interface_error_count" for finding in snapshot.findings
    )
