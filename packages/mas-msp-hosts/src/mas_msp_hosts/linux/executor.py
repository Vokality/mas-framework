"""Typed Linux host remediation executor."""

from __future__ import annotations

from datetime import UTC, datetime

from mas_msp_contracts import AssetKind, RemediationExecute, RemediationResult
from mas_msp_contracts.types import JSONObject

from ..common import HOST_SERVICE_ACTIONS, HostServiceRegistry


class LinuxExecutorAgent:
    """Execute typed Linux service-control remediations."""

    def __init__(self, *, service_registry: HostServiceRegistry | None = None) -> None:
        self._service_registry = service_registry or HostServiceRegistry()

    async def execute_remediation(
        self,
        request: RemediationExecute,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> RemediationResult:
        """Execute one typed Linux remediation request."""

        del recent_activity
        if request.asset.asset_kind is not AssetKind.LINUX_HOST:
            raise ValueError("linux executor requires a linux_host asset")
        if request.action_type not in HOST_SERVICE_ACTIONS:
            raise ValueError(f"unsupported Linux action_type: {request.action_type}")
        service_name = request.parameters.get("service_name")
        if not isinstance(service_name, str) or not service_name.strip():
            raise ValueError("linux remediations require a service_name parameter")
        post_state = self._service_registry.apply_action(
            asset=request.asset,
            action_type=request.action_type,
            service_name=service_name.strip(),
        )
        completed_at = datetime.now(UTC)
        return RemediationResult(
            request_id=request.request_id,
            incident_id=request.incident_id,
            client_id=request.client_id,
            fabric_id=request.fabric_id,
            asset=request.asset,
            completed_at=completed_at,
            outcome="completed",
            audit_reference=(
                f"linux-service:{request.asset.asset_id}:{service_name}:{request.action_type}"
            ),
            post_state=post_state,
        )


__all__ = ["LinuxExecutorAgent"]
