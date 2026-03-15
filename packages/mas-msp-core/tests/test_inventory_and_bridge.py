from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from mas_msp_contracts import (
    AlertRaised,
    AssetKind,
    AssetRef,
    HealthSnapshot,
    HealthState,
    Severity,
)
from mas_msp_core import (
    InventoryAgent,
    InventoryRepository,
    OpsBridgeAgent,
    build_ops_plane_connector_id,
)
from mas_msp_hosts.common import build_host_asset_ref


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"


def _candidate_asset(
    *, hostname: str, mgmt_address: str, site: str = "nyc-1"
) -> AssetRef:
    return AssetRef(
        asset_id=str(uuid4()),
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.NETWORK_DEVICE,
        vendor="Cisco",
        model="Catalyst 9300",
        hostname=hostname,
        mgmt_address=mgmt_address,
        site=site,
        tags=["core"],
    )


def _alert(*, asset: AssetRef, serial: str | None = None) -> AlertRaised:
    facts: dict[str, object] = {"interface": "Gi1/0/48"}
    if serial is not None:
        facts["serial"] = serial
    return AlertRaised(
        alert_id=str(uuid4()),
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset=asset,
        source_kind="syslog",
        occurred_at=datetime(2026, 3, 15, 14, 30, tzinfo=UTC),
        severity=Severity.MAJOR,
        category="interface",
        title="Uplink flap detected",
        normalized_facts=facts,
    )


def _snapshot(
    *, asset: AssetRef, serial: str | None = None, health_state: HealthState
) -> HealthSnapshot:
    metrics: dict[str, object] = {"cpu_percent": 87}
    if serial is not None:
        metrics["serial"] = serial
    return HealthSnapshot(
        snapshot_id=str(uuid4()),
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset=asset,
        source_kind="snmp_poll",
        collected_at=datetime(2026, 3, 15, 14, 45, tzinfo=UTC),
        health_state=health_state,
        metrics=metrics,
        findings=[],
    )


def test_inventory_repository_prefers_mgmt_address_then_serial_then_hostname() -> None:
    repository = InventoryRepository()

    first_publish = repository.process_alert(
        _alert(asset=_candidate_asset(hostname="edge-sw-01", mgmt_address="10.0.0.10"))
    )
    second_publish = repository.process_alert(
        _alert(
            asset=_candidate_asset(hostname="renamed-sw-01", mgmt_address="10.0.0.10")
        )
    )
    third_publish = repository.process_snapshot(
        _snapshot(
            asset=_candidate_asset(hostname="edge-sw-serial", mgmt_address="10.0.0.11"),
            serial="FTX1234ABC",
            health_state=HealthState.DEGRADED,
        )
    )
    fourth_publish = repository.process_alert(
        _alert(
            asset=_candidate_asset(hostname="another-name", mgmt_address="10.0.0.12"),
            serial="FTX1234ABC",
        )
    )
    fifth_publish = repository.process_alert(
        _alert(
            asset=_candidate_asset(hostname="edge-dist-01", mgmt_address="10.0.0.20")
        )
    )
    sixth_publish = repository.process_snapshot(
        _snapshot(
            asset=_candidate_asset(hostname="edge-dist-01", mgmt_address="10.0.0.21"),
            health_state=HealthState.CRITICAL,
        )
    )

    assert second_publish.asset.asset_id == first_publish.asset.asset_id
    assert second_publish.asset.hostname == "renamed-sw-01"
    assert fourth_publish.asset.asset_id == third_publish.asset.asset_id
    assert sixth_publish.asset.asset_id == fifth_publish.asset.asset_id
    assert sixth_publish.health_changed is True


def test_inventory_repository_assigns_inventory_owned_asset_id_for_new_assets() -> None:
    repository = InventoryRepository()
    candidate = _candidate_asset(hostname="edge-sw-01", mgmt_address="10.0.0.10")

    publish = repository.process_alert(_alert(asset=candidate))

    assert publish.asset.asset_id != candidate.asset_id


def test_host_asset_ids_are_stable_across_repository_restarts() -> None:
    candidate = build_host_asset_ref(
        asset_kind=AssetKind.LINUX_HOST,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        vendor="Linux",
        model="Docker Linux",
        hostname="mas-runtime",
        mgmt_address="docker://mas-runtime",
        site="docker-compose",
        tags=["docker", "mas-system"],
    )
    first_publish = InventoryRepository().process_snapshot(
        _snapshot(asset=candidate, health_state=HealthState.HEALTHY)
    )
    second_publish = InventoryRepository().process_snapshot(
        _snapshot(
            asset=build_host_asset_ref(
                asset_kind=AssetKind.LINUX_HOST,
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                vendor="Linux",
                model="Docker Linux",
                hostname="mas-runtime",
                mgmt_address="docker://mas-runtime",
                site="docker-compose",
                tags=["docker", "mas-system"],
            ),
            health_state=HealthState.CRITICAL,
        )
    )

    assert second_publish.asset.asset_id == first_publish.asset.asset_id


async def test_inventory_agent_forwards_resolved_payloads() -> None:
    agent = InventoryAgent(repository=InventoryRepository())
    agent.send = AsyncMock()

    alert = _alert(
        asset=_candidate_asset(hostname="edge-sw-01", mgmt_address="10.0.0.10")
    )
    await agent.handle_alert(AsyncMock(), alert)

    agent.send.assert_awaited_once()
    target_id, message_type, payload = agent.send.await_args.args
    assert target_id == "ops-bridge"
    assert message_type == "portfolio.publish"
    assert payload["source"]["asset"]["mgmt_address"] == "10.0.0.10"


def test_ops_bridge_emits_asset_and_alert_events() -> None:
    repository = InventoryRepository()
    publish_request = repository.process_alert(
        _alert(asset=_candidate_asset(hostname="edge-sw-01", mgmt_address="10.0.0.10"))
    )
    bridge = OpsBridgeAgent()

    events = bridge.build_portfolio_events(publish_request)

    assert [event.event_type for event in events] == [
        "asset.upserted",
        "network.alert.raised",
    ]
    assert events[0].subject_id == publish_request.asset.asset_id
    assert events[0].payload["source_reference"] == {
        "source_type": "alert",
        "source_id": publish_request.source.alert_id,
    }
    assert events[1].subject_type == "alert"


async def test_ops_bridge_routes_events_to_matching_connector() -> None:
    repository = InventoryRepository()
    publish_request = repository.process_snapshot(
        _snapshot(
            asset=_candidate_asset(hostname="edge-sw-01", mgmt_address="10.0.0.10"),
            health_state=HealthState.CRITICAL,
        )
    )
    bridge = OpsBridgeAgent()
    bridge.send = AsyncMock()

    await bridge.handle_portfolio_publish(AsyncMock(), publish_request)

    targets = [call.args[0] for call in bridge.send.await_args_list]
    assert targets == [build_ops_plane_connector_id(CLIENT_ID)] * 3
