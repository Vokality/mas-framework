"""Linux host polling agent."""

from __future__ import annotations

from uuid import uuid4

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import AssetKind, HealthSnapshot

from mas_msp_core import INVENTORY_AGENT_ID, LINUX_POLLING_AGENT_ID

from ..common import build_host_asset_ref, evaluate_host_health
from .models import LinuxHostPoller, LinuxPollObservation, LinuxPollingTarget


class LinuxPollingAgent(Agent[dict[str, object]]):
    """Poll Linux hosts and emit normalized health snapshots."""

    def __init__(
        self,
        *,
        poller: LinuxHostPoller,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            LINUX_POLLING_AGENT_ID,
            capabilities=["msp:hosts:linux:poll"],
            server_addr=server_addr,
            tls=tls,
        )
        self._poller = poller

    def normalize_poll(
        self,
        target: LinuxPollingTarget,
        observation: LinuxPollObservation,
    ) -> HealthSnapshot:
        """Normalize one Linux polling observation into a shared snapshot."""

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
                asset_kind=AssetKind.LINUX_HOST,
                client_id=target.client_id,
                fabric_id=target.fabric_id,
                vendor="Linux",
                model=target.distribution,
                hostname=target.hostname,
                mgmt_address=target.mgmt_address,
                site=target.site,
                tags=[*target.tags, "linux"],
            ),
            source_kind="ssh_poll",
            collected_at=observation.collected_at,
            health_state=health_state,
            metrics={
                **dict(observation.metrics),
                "services": [dict(item) for item in observation.services],
            },
            findings=findings,
        )

    async def poll_target(self, target: LinuxPollingTarget) -> HealthSnapshot:
        """Poll one Linux host and forward the normalized health snapshot."""

        observation = await self._poller.poll(target)
        snapshot = self.normalize_poll(target, observation)
        await self.send(
            INVENTORY_AGENT_ID,
            "health.snapshot",
            snapshot.model_dump(mode="json"),
        )
        return snapshot


__all__ = ["LinuxPollingAgent"]
