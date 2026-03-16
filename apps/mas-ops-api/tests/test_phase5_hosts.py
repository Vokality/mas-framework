"""Phase 5 host visibility, remediation, and verification tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mas_msp_contracts import (
    AlertRaised,
    AssetKind,
    AssetRef,
    ConfigDesiredState,
    HealthSnapshot,
    HealthState,
    Severity,
)
from mas_msp_core import AppliedAlertConfiguration
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.models import (
    AppliedAlertPolicyRecord,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)

from .conftest import CLIENT_A, FABRIC_A
from .test_portfolio_ingest import _seed_desired_state


HOST_ASSET_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
HOST_ALERT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
HOST_SNAPSHOT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _linux_host_asset() -> AssetRef:
    return AssetRef(
        asset_id=HOST_ASSET_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset_kind=AssetKind.LINUX_HOST,
        vendor="Linux",
        model="Ubuntu 24.04",
        hostname="web-01",
        mgmt_address="10.20.0.15",
        site="nyc-1",
        tags=["linux", "production"],
    )


def _linux_host_alert() -> AlertRaised:
    return AlertRaised(
        alert_id=HOST_ALERT_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset=_linux_host_asset(),
        source_kind="journald",
        occurred_at=datetime(2026, 3, 15, 15, 0, tzinfo=UTC),
        severity=Severity.MAJOR,
        category="service",
        title="nginx on web-01 entered failed state",
        normalized_facts={"service_name": "nginx"},
    )


def _linux_host_snapshot() -> HealthSnapshot:
    return HealthSnapshot(
        snapshot_id=HOST_SNAPSHOT_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset=_linux_host_asset(),
        source_kind="ssh_poll",
        collected_at=datetime(2026, 3, 15, 15, 2, tzinfo=UTC),
        health_state=HealthState.UNKNOWN,
        metrics={
            "cpu_percent": 41,
            "memory_percent": 58,
            "disk_percent": 70,
            "services": [
                {"service_name": "nginx", "service_state": "failed"},
                {"service_name": "sshd", "service_state": "running"},
            ],
        },
        findings=[],
    )


async def _wait_for_turn_state(
    api_client,
    chat_session_id: str,
    expected_state: str,
) -> dict[str, object]:  # noqa: ANN001
    for _ in range(40):
        response = await api_client.get(f"/chat/sessions/{chat_session_id}")
        assert response.status_code == 200
        session = response.json()
        if session["turns"] and session["turns"][-1]["state"] == expected_state:
            return session
        await asyncio.sleep(0.01)
    raise AssertionError(f"chat turn did not reach {expected_state!r} in time")


async def _apply_host_service_policy(session_factory) -> None:  # noqa: ANN001
    configuration = AppliedAlertConfiguration.from_desired_state(
        ConfigDesiredState(
            client_id=CLIENT_A,
            fabric_id=FABRIC_A,
            desired_state_version=1,
            tenant_metadata={},
            policy={
                "alerting": {
                    "host_defaults": {
                        "services": {
                            "watch": ["nginx"],
                        }
                    }
                }
            },
            inventory_sources=[],
            notification_routes=[],
        )
    )
    async with session_factory() as session:
        session.add(
            AppliedAlertPolicyRecord(
                client_id=CLIENT_A,
                configuration=configuration.model_dump(mode="json"),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_host_visibility_projects_linux_assets_and_incidents(
    ops_app,
    session_factory,
) -> None:
    await _seed_desired_state(session_factory)
    await _apply_host_service_policy(session_factory)
    await ops_app.state.services.visibility_runtime.ingest_contract(_linux_host_alert())
    await ops_app.state.services.visibility_runtime.ingest_contract(
        _linux_host_snapshot()
    )

    async with session_factory() as session:
        client = await session.get(PortfolioClient, CLIENT_A)
        asset = (
            await session.scalars(
                select(PortfolioAsset).where(PortfolioAsset.client_id == CLIENT_A)
            )
        ).one()
        incidents = list((await session.scalars(select(PortfolioIncident))).all())

    assert client is not None
    assert client.open_alert_count == 1
    assert asset is not None
    assert asset.asset_kind == AssetKind.LINUX_HOST.value
    assert asset.health_state == HealthState.CRITICAL.value
    assert len(incidents) == 1
    assert incidents[0].summary == "nginx on web-01 entered failed state"


@pytest.mark.asyncio
async def test_host_remediation_executes_and_verifies_before_resolution(
    api_client,
    ops_app,
    seed_user,
    login,
    session_factory,
) -> None:
    await _seed_desired_state(session_factory)
    await ops_app.state.services.visibility_runtime.ingest_contract(_linux_host_alert())

    async with session_factory() as session:
        incident = (
            await session.scalars(
                select(PortfolioIncident).where(
                    PortfolioIncident.summary == "nginx on web-01 entered failed state"
                )
            )
        ).one()

    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await login("operator@example.com", "password-1")

    create_response = await api_client.post(
        "/chat/sessions",
        json={
            "scope": "incident",
            "client_id": CLIENT_A,
            "fabric_id": FABRIC_A,
            "incident_id": incident.incident_id,
        },
    )
    assert create_response.status_code == 201
    chat_session_id = create_response.json()["chat_session_id"]

    append_response = await api_client.post(
        f"/chat/sessions/{chat_session_id}/messages",
        json={"message": "Restart nginx service on the web host."},
    )
    assert append_response.status_code == 201

    waiting_session = await _wait_for_turn_state(
        api_client,
        chat_session_id,
        "waiting_for_approval",
    )
    approval_id = waiting_session["turns"][-1]["approval_id"]
    assert approval_id is not None

    decision_response = await api_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve", "reason": "Proceed"},
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["state"] == "executed"

    completed_session = await _wait_for_turn_state(
        api_client,
        chat_session_id,
        "completed",
    )
    assert "resolved" in completed_session["messages"][-1]["content"].lower()

    incident_response = await api_client.get(f"/incidents/{incident.incident_id}")
    assert incident_response.status_code == 200
    payload = incident_response.json()
    assert payload["state"] == "resolved"
    assert payload["assets"][0]["asset_kind"] == "linux_host"
    assert payload["evidence_bundles"]
    assert {item["event_type"] for item in payload["activity"]} >= {
        "remediation.started",
        "remediation.executed",
        "remediation.verified",
    }
    approval = next(
        item for item in payload["approvals"] if item["approval_id"] == approval_id
    )
    assert approval["action_kind"] == "host.remediation"
    assert approval["state"] == "executed"
