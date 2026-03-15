from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mas_msp_contracts import (
    AssetKind,
    AssetRef,
    DiagnosticsCollect,
)
from mas_msp_network import NetworkDiagnosticsAgent


def _request() -> DiagnosticsCollect:
    asset = AssetRef(
        asset_id="77777777-7777-4777-8777-777777777777",
        client_id="11111111-1111-4111-8111-111111111111",
        fabric_id="22222222-2222-4222-8222-222222222222",
        asset_kind=AssetKind.NETWORK_DEVICE,
        vendor="Cisco",
        model="Catalyst 9300",
        hostname="edge-sw-01",
        mgmt_address="10.0.0.10",
        site="nyc-1",
        tags=["core"],
    )
    return DiagnosticsCollect(
        request_id="33333333-3333-4333-8333-333333333333",
        incident_id="44444444-4444-4444-8444-444444444444",
        client_id=asset.client_id,
        fabric_id=asset.fabric_id,
        asset=asset,
        diagnostic_profile="uplink-health",
        requested_actions=["collect-interface-status", "collect-health-snapshot"],
        timeout_seconds=30,
        read_only=True,
    )


@pytest.mark.asyncio
async def test_network_diagnostics_agent_returns_evidence_bundle() -> None:
    agent = NetworkDiagnosticsAgent()

    execution = await agent.execute_diagnostics(
        _request(),
        recent_activity=[
            {
                "event_type": "network.alert.raised",
                "occurred_at": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
                "payload": {"title": "Primary uplink changed state to down"},
            }
        ],
    )

    assert execution.result.outcome == "completed"
    assert (
        execution.result.evidence_bundle_id
        == execution.evidence_bundle.evidence_bundle_id
    )
    assert execution.result.structured_results["read_only"] is True
    assert execution.evidence_bundle.summary
    assert execution.evidence_bundle.items
