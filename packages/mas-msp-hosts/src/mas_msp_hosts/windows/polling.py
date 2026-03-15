"""Windows host polling agent."""

from __future__ import annotations

from uuid import uuid4

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import AssetKind, HealthSnapshot

from mas_msp_core import INVENTORY_AGENT_ID, WINDOWS_POLLING_AGENT_ID

from ..common import build_host_asset_ref, evaluate_host_health
from .models import WindowsHostPoller, WindowsPollObservation, WindowsPollingTarget


class WindowsPollingAgent(Agent[dict[str, object]]):
    """Poll Windows hosts and emit normalized health snapshots."""

    def __init__(
        self,
        *,
        poller: WindowsHostPoller,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            WINDOWS_POLLING_AGENT_ID,
            capabilities=["msp:hosts:windows:poll"],
            server_addr=server_addr,
            tls=tls,
        )
        self._poller = poller

    def normalize_poll(
        self,
        target: WindowsPollingTarget,
        observation: WindowsPollObservation,
    ) -> HealthSnapshot:
        """Normalize one Windows polling observation into a shared snapshot."""

        health_state, findings = evaluate_host_health(
            metrics=dict(observation.metrics),
            services=[dict(item) for item in observation.services],
            findings=observation.findings,
        )
        return HealthSnapshot(
            snapshot_id=str(uuid4()),
            client_id=target.client_id,
            fabric_id=target.fabric_id,
            asset=build_host_asset_ref(
                asset_kind=AssetKind.WINDOWS_HOST,
                client_id=target.client_id,
                fabric_id=target.fabric_id,
                vendor="Windows",
                model=target.edition,
                hostname=target.hostname,
                mgmt_address=target.mgmt_address,
                site=target.site,
                tags=[*target.tags, "windows"],
            ),
            source_kind="wmi_poll",
            collected_at=observation.collected_at,
            health_state=health_state,
            metrics={
                **dict(observation.metrics),
                "services": [dict(item) for item in observation.services],
            },
            findings=findings,
        )

    async def poll_target(self, target: WindowsPollingTarget) -> HealthSnapshot:
        """Poll one Windows host and forward the normalized health snapshot."""

        observation = await self._poller.poll(target)
        snapshot = self.normalize_poll(target, observation)
        await self.send(
            INVENTORY_AGENT_ID,
            "health.snapshot",
            snapshot.model_dump(mode="json"),
        )
        return snapshot


__all__ = ["WindowsPollingAgent"]
