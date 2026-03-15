"""Tests for reserved MSP logical agent identifiers."""

from __future__ import annotations

from mas_msp_core import (
    CORE_ORCHESTRATOR_AGENT_ID,
    OPS_BRIDGE_AGENT_ID,
    build_ops_plane_connector_id,
    is_ops_plane_connector_id,
    is_reserved_agent_id,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"


def test_build_ops_plane_connector_id_uses_reserved_format() -> None:
    assert build_ops_plane_connector_id(CLIENT_ID) == f"ops-plane-{CLIENT_ID}"


def test_is_ops_plane_connector_id_requires_uuid_suffix() -> None:
    assert is_ops_plane_connector_id(f"ops-plane-{CLIENT_ID}") is True
    assert is_ops_plane_connector_id("ops-plane-not-a-uuid") is False


def test_is_reserved_agent_id_covers_static_and_connector_ids() -> None:
    assert is_reserved_agent_id(OPS_BRIDGE_AGENT_ID) is True
    assert is_reserved_agent_id(CORE_ORCHESTRATOR_AGENT_ID) is True
    assert is_reserved_agent_id(f"ops-plane-{CLIENT_ID}") is True
    assert is_reserved_agent_id("custom-agent") is False
