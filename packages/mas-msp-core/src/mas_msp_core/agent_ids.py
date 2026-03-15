"""Reserved logical agent identifiers for the MSP architecture."""

from __future__ import annotations

from mas_msp_contracts.types import ClientId, validate_uuid4_string


OPS_BRIDGE_AGENT_ID = "ops-bridge"
INVENTORY_AGENT_ID = "inventory"
NETWORK_EVENT_INGEST_AGENT_ID = "network-event-ingest"
NETWORK_POLLING_AGENT_ID = "network-polling"
CORE_ORCHESTRATOR_AGENT_ID = "core-orchestrator"
NETWORK_DIAGNOSTICS_AGENT_ID = "network-diagnostics"
NOTIFIER_TRANSPORT_AGENT_ID = "notifier-transport"
APPROVAL_CONTROLLER_AGENT_ID = "approval-controller"
CONFIG_DEPLOYER_AGENT_ID = "config-deployer"
LINUX_EVENT_INGEST_AGENT_ID = "linux-event-ingest"
LINUX_POLLING_AGENT_ID = "linux-polling"
LINUX_DIAGNOSTICS_AGENT_ID = "linux-diagnostics"
LINUX_EXECUTOR_AGENT_ID = "linux-executor"
WINDOWS_EVENT_INGEST_AGENT_ID = "windows-event-ingest"
WINDOWS_POLLING_AGENT_ID = "windows-polling"
WINDOWS_DIAGNOSTICS_AGENT_ID = "windows-diagnostics"
WINDOWS_EXECUTOR_AGENT_ID = "windows-executor"

OPS_PLANE_CONNECTOR_PREFIX = "ops-plane-"

RESERVED_STATIC_AGENT_IDS = frozenset(
    {
        OPS_BRIDGE_AGENT_ID,
        INVENTORY_AGENT_ID,
        NETWORK_EVENT_INGEST_AGENT_ID,
        NETWORK_POLLING_AGENT_ID,
        CORE_ORCHESTRATOR_AGENT_ID,
        NETWORK_DIAGNOSTICS_AGENT_ID,
        NOTIFIER_TRANSPORT_AGENT_ID,
        APPROVAL_CONTROLLER_AGENT_ID,
        CONFIG_DEPLOYER_AGENT_ID,
        LINUX_EVENT_INGEST_AGENT_ID,
        LINUX_POLLING_AGENT_ID,
        LINUX_DIAGNOSTICS_AGENT_ID,
        LINUX_EXECUTOR_AGENT_ID,
        WINDOWS_EVENT_INGEST_AGENT_ID,
        WINDOWS_POLLING_AGENT_ID,
        WINDOWS_DIAGNOSTICS_AGENT_ID,
        WINDOWS_EXECUTOR_AGENT_ID,
    }
)


def build_ops_plane_connector_id(client_id: ClientId) -> str:
    """Return the reserved ops-plane connector identity for a client."""

    return f"{OPS_PLANE_CONNECTOR_PREFIX}{client_id}"


def is_ops_plane_connector_id(agent_id: str) -> bool:
    """Return whether an identifier matches the reserved connector format."""

    if not agent_id.startswith(OPS_PLANE_CONNECTOR_PREFIX):
        return False

    suffix = agent_id.removeprefix(OPS_PLANE_CONNECTOR_PREFIX)
    try:
        validate_uuid4_string(suffix)
    except ValueError:
        return False
    return True


def is_reserved_agent_id(agent_id: str) -> bool:
    """Return whether an identifier is reserved by the MSP architecture."""

    return agent_id in RESERVED_STATIC_AGENT_IDS or is_ops_plane_connector_id(agent_id)


__all__ = [
    "APPROVAL_CONTROLLER_AGENT_ID",
    "CONFIG_DEPLOYER_AGENT_ID",
    "CORE_ORCHESTRATOR_AGENT_ID",
    "INVENTORY_AGENT_ID",
    "LINUX_DIAGNOSTICS_AGENT_ID",
    "LINUX_EVENT_INGEST_AGENT_ID",
    "LINUX_EXECUTOR_AGENT_ID",
    "LINUX_POLLING_AGENT_ID",
    "NETWORK_DIAGNOSTICS_AGENT_ID",
    "NETWORK_EVENT_INGEST_AGENT_ID",
    "NETWORK_POLLING_AGENT_ID",
    "NOTIFIER_TRANSPORT_AGENT_ID",
    "OPS_BRIDGE_AGENT_ID",
    "OPS_PLANE_CONNECTOR_PREFIX",
    "RESERVED_STATIC_AGENT_IDS",
    "WINDOWS_DIAGNOSTICS_AGENT_ID",
    "WINDOWS_EVENT_INGEST_AGENT_ID",
    "WINDOWS_EXECUTOR_AGENT_ID",
    "WINDOWS_POLLING_AGENT_ID",
    "build_ops_plane_connector_id",
    "is_ops_plane_connector_id",
    "is_reserved_agent_id",
]
