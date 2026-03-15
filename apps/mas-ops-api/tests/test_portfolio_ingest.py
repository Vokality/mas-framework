"""Phase 2 projection and connector tests for network visibility."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mas_msp_contracts import (
    AlertRaised,
    AssetKind,
    AssetRef,
    HealthSnapshot,
    HealthState,
    PortfolioEvent,
    Severity,
)
from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.models import (
    ConfigDesiredStateRecord,
    OpsStreamEvent,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)
from mas_ops_api.projections.source_ids import build_projection_source_id

from .conftest import CLIENT_A, FABRIC_A


ASSET_ID = "77777777-7777-4777-8777-777777777777"
ALERT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SNAPSHOT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ASSET_UPSERT_EVENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ALERT_EVENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
HEALTH_EVENT_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
SNAPSHOT_EVENT_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
OLDER_SNAPSHOT_EVENT_ID = "99999999-9999-4999-8999-999999999998"
OLDER_SNAPSHOT_ID = "abababab-abab-4bab-8bab-abababababab"


def _asset_ref() -> AssetRef:
    return AssetRef(
        asset_id=ASSET_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset_kind=AssetKind.NETWORK_DEVICE,
        vendor="Cisco",
        model="Catalyst 9300",
        hostname="edge-sw-01",
        mgmt_address="10.0.0.10",
        site="nyc-1",
        tags=["core", "distribution"],
    )


def _asset_upsert_event() -> PortfolioEvent:
    asset = _asset_ref()
    return PortfolioEvent(
        event_id=ASSET_UPSERT_EVENT_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        event_type="asset.upserted",
        subject_type="asset",
        subject_id=asset.asset_id,
        occurred_at=datetime(2026, 3, 15, 14, 0, tzinfo=UTC),
        payload_version=1,
        payload={
            "asset": asset.model_dump(mode="json"),
            "source_reference": {
                "source_type": "alert",
                "source_id": ALERT_ID,
            },
        },
    )


def _alert_event() -> PortfolioEvent:
    alert = AlertRaised(
        alert_id=ALERT_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset=_asset_ref(),
        source_kind="syslog",
        occurred_at=datetime(2026, 3, 15, 14, 5, tzinfo=UTC),
        severity=Severity.MAJOR,
        category="interface",
        title="Primary uplink changed state to down",
        normalized_facts={"interface": "Gi1/0/48"},
    )
    return PortfolioEvent(
        event_id=ALERT_EVENT_ID,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        event_type="network.alert.raised",
        subject_type="alert",
        subject_id=alert.alert_id,
        occurred_at=alert.occurred_at,
        payload_version=1,
        payload={"alert": alert.model_dump(mode="json")},
    )


def _health_event(
    *,
    event_id: str,
    collected_at: datetime,
    health_state: HealthState,
    snapshot_id: str = SNAPSHOT_ID,
) -> PortfolioEvent:
    snapshot = HealthSnapshot(
        snapshot_id=snapshot_id,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset=_asset_ref(),
        source_kind="snmp_poll",
        collected_at=collected_at,
        health_state=health_state,
        metrics={"cpu_percent": 94, "serial": "FTX1234ABC"},
        findings=[{"code": "cpu_critical", "metric": "cpu_percent", "value": 94}],
    )
    return PortfolioEvent(
        event_id=event_id,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        event_type="asset.health.changed",
        subject_type="asset",
        subject_id=snapshot.asset.asset_id,
        occurred_at=snapshot.collected_at,
        payload_version=1,
        payload={
            "asset": snapshot.asset.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
        },
    )


def _snapshot_event(
    *,
    event_id: str,
    collected_at: datetime,
    health_state: HealthState,
    snapshot_id: str = SNAPSHOT_ID,
) -> PortfolioEvent:
    snapshot = HealthSnapshot(
        snapshot_id=snapshot_id,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        asset=_asset_ref(),
        source_kind="snmp_poll",
        collected_at=collected_at,
        health_state=health_state,
        metrics={"cpu_percent": 42, "serial": "FTX1234ABC"},
        findings=[],
    )
    return PortfolioEvent(
        event_id=event_id,
        client_id=CLIENT_A,
        fabric_id=FABRIC_A,
        event_type="network.snapshot.recorded",
        subject_type="snapshot",
        subject_id=snapshot.snapshot_id,
        occurred_at=snapshot.collected_at,
        payload_version=1,
        payload={"snapshot": snapshot.model_dump(mode="json")},
    )


async def _seed_desired_state(session_factory) -> None:  # noqa: ANN001
    async with session_factory() as session:
        session.add(
            ConfigDesiredStateRecord(
                client_id=CLIENT_A,
                fabric_id=FABRIC_A,
                desired_state_version=1,
                tenant_metadata={"display_name": "Acme Corp"},
                policy={"default_mode": "deny"},
                inventory_sources=[{"kind": "snmp"}],
                notification_routes=[{"kind": "email"}],
                updated_at=datetime(2026, 3, 15, 13, 55, tzinfo=UTC),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_portfolio_connector_ingests_visibility_events_idempotently(
    ops_app,
    session_factory,
) -> None:
    await _seed_desired_state(session_factory)
    connector = ops_app.state.services.portfolio_ingress_registry.get(CLIENT_A)
    asset_upsert_event = _asset_upsert_event()
    duplicate_asset_upsert_event = asset_upsert_event.model_copy(
        update={"event_id": "12121212-1212-4121-8121-121212121212"}
    )
    alert_event = _alert_event()
    duplicate_alert_event = alert_event.model_copy(
        update={"event_id": "13131313-1313-4131-8131-131313131313"}
    )
    health_event = _health_event(
        event_id=HEALTH_EVENT_ID,
        collected_at=datetime(2026, 3, 15, 14, 10, tzinfo=UTC),
        health_state=HealthState.CRITICAL,
    )
    snapshot_event = _snapshot_event(
        event_id=SNAPSHOT_EVENT_ID,
        collected_at=datetime(2026, 3, 15, 14, 11, tzinfo=UTC),
        health_state=HealthState.CRITICAL,
    )

    await connector.ingest_portfolio_event(event=asset_upsert_event)
    await connector.ingest_portfolio_event(event=alert_event)
    await connector.ingest_portfolio_event(event=health_event)
    await connector.ingest_portfolio_event(event=snapshot_event)
    await connector.ingest_portfolio_event(event=duplicate_asset_upsert_event)
    await connector.ingest_portfolio_event(event=duplicate_alert_event)
    await connector.ingest_portfolio_event(
        event=_snapshot_event(
            event_id=OLDER_SNAPSHOT_EVENT_ID,
            collected_at=datetime(2026, 3, 15, 14, 1, tzinfo=UTC),
            health_state=HealthState.HEALTHY,
            snapshot_id=OLDER_SNAPSHOT_ID,
        )
    )

    async with session_factory() as session:
        client = await session.get(PortfolioClient, CLIENT_A)
        asset = await session.get(PortfolioAsset, ASSET_ID)
        incidents = list((await session.scalars(select(PortfolioIncident))).all())
        activity_rows = list(
            (
                await session.scalars(
                    select(PortfolioActivityEvent).order_by(
                        PortfolioActivityEvent.activity_id.asc()
                    )
                )
            ).all()
        )
        stream_rows = list((await session.scalars(select(OpsStreamEvent))).all())

    assert client is not None
    assert client.name == "Acme Corp"
    assert client.open_alert_count == 1
    assert client.critical_asset_count == 1

    assert asset is not None
    assert asset.health_state == HealthState.CRITICAL.value
    assert asset.health_observed_at == datetime(2026, 3, 15, 14, 11, tzinfo=UTC)
    assert asset.last_alert_at == datetime(2026, 3, 15, 14, 5, tzinfo=UTC)

    assert len(incidents) == 1
    assert incidents[0].summary == "Primary uplink changed state to down"
    assert incidents[0].state == "open"

    assert [row.source_event_id for row in activity_rows] == [
        build_projection_source_id(asset_upsert_event),
        build_projection_source_id(alert_event),
        f"incident-alert:{ALERT_ID}",
        build_projection_source_id(health_event),
        build_projection_source_id(snapshot_event),
        build_projection_source_id(
            _snapshot_event(
                event_id=OLDER_SNAPSHOT_EVENT_ID,
                collected_at=datetime(2026, 3, 15, 14, 1, tzinfo=UTC),
                health_state=HealthState.HEALTHY,
                snapshot_id=OLDER_SNAPSHOT_ID,
            )
        ),
    ]
    assert all(row.asset_id == ASSET_ID for row in activity_rows)
    assert len(stream_rows) == 15
    assert len(activity_rows) == 6
    assert stream_rows[0].payload["updated_at"] == "2026-03-15T14:00:00Z"
    assert stream_rows[1].payload["source_event_id"] == build_projection_source_id(
        asset_upsert_event
    )
    assert stream_rows[2].payload["updated_at"] == "2026-03-15T14:00:00Z"


@pytest.mark.asyncio
async def test_portfolio_connector_does_not_bootstrap_desired_state_for_new_clients(
    ops_app,
    session_factory,
) -> None:
    connector = ops_app.state.services.portfolio_ingress_registry.get(CLIENT_A)

    await connector.ingest_portfolio_event(event=_alert_event())

    async with session_factory() as session:
        client = await session.get(PortfolioClient, CLIENT_A)
        desired_state = await session.get(ConfigDesiredStateRecord, CLIENT_A)
        stream_rows = list(
            (
                await session.scalars(
                    select(OpsStreamEvent).where(
                        OpsStreamEvent.subject_type == "config_desired_state"
                    )
                )
            ).all()
        )

    assert client is not None
    assert client.name == f"Client {CLIENT_A[:8]}"
    assert desired_state is None
    assert stream_rows == []


@pytest.mark.asyncio
async def test_phase_2_routes_expose_client_and_asset_activity(
    api_client,
    ops_app,
    seed_user,
    login,
    session_factory,
) -> None:
    await _seed_desired_state(session_factory)
    connector = ops_app.state.services.portfolio_ingress_registry.get(CLIENT_A)
    await connector.ingest_portfolio_event(event=_asset_upsert_event())
    await connector.ingest_portfolio_event(event=_alert_event())
    await connector.ingest_portfolio_event(
        event=_snapshot_event(
            event_id=SNAPSHOT_EVENT_ID,
            collected_at=datetime(2026, 3, 15, 14, 11, tzinfo=UTC),
            health_state=HealthState.DEGRADED,
        )
    )

    await seed_user(
        email="operator@example.com",
        password="password-1",
        role=UserRole.OPERATOR,
        client_ids=(CLIENT_A,),
    )
    await login("operator@example.com", "password-1")

    client_activity = await api_client.get(f"/clients/{CLIENT_A}/activity")
    assert client_activity.status_code == 200
    assert [item["event_type"] for item in client_activity.json()] == [
        "network.snapshot.recorded",
        "incident.updated",
        "network.alert.raised",
        "asset.upserted",
    ]
    assert all(item["asset_id"] == ASSET_ID for item in client_activity.json())

    asset_response = await api_client.get(f"/assets/{ASSET_ID}")
    assert asset_response.status_code == 200
    assert asset_response.json()["health_state"] == "degraded"
    assert asset_response.json()["health_observed_at"] == "2026-03-15T14:11:00Z"
    assert asset_response.json()["last_alert_at"] == "2026-03-15T14:05:00Z"

    asset_activity = await api_client.get(f"/assets/{ASSET_ID}/activity")
    assert asset_activity.status_code == 200
    assert [item["event_type"] for item in asset_activity.json()] == [
        "network.snapshot.recorded",
        "incident.updated",
        "network.alert.raised",
        "asset.upserted",
    ]
