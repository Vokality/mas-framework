"""Inventory agent for deterministic asset binding."""

from __future__ import annotations

from mas_agent import Agent, AgentMessage, TlsClientConfig
from mas_msp_contracts import AlertRaised, AssetKind, HealthSnapshot

from mas_msp_core.alerting import AlertPolicyAgent
from mas_msp_core.agent_ids import INVENTORY_AGENT_ID, OPS_BRIDGE_AGENT_ID
from mas_msp_core.messages import PortfolioPublish
from mas_msp_core.inventory.repository import InventoryRepository


class InventoryAgent(Agent[dict[str, object]]):
    """Bind visibility payloads to stable assets and publish bridge messages."""

    def __init__(
        self,
        *,
        repository: InventoryRepository | None = None,
        alert_policy_agent: AlertPolicyAgent | None = None,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            INVENTORY_AGENT_ID,
            capabilities=["msp:inventory"],
            server_addr=server_addr,
            tls=tls,
        )
        self.repository = repository or InventoryRepository()
        self._alert_policy_agent = alert_policy_agent

    @Agent.on("alert.raised", model=AlertRaised)
    async def handle_alert(
        self,
        _message: AgentMessage,
        alert: AlertRaised,
    ) -> None:
        bound_alert = self.repository.bind_alert(alert)
        surfaced_alerts = (
            [bound_alert.alert]
            if self._alert_policy_agent is None
            else await self._alert_policy_agent.process_source_alert(bound_alert.alert)
        )
        for index, surfaced_alert in enumerate(surfaced_alerts):
            await self._publish(
                PortfolioPublish(
                    asset=surfaced_alert.asset,
                    asset_upserted=bound_alert.asset_upserted and index == 0,
                    health_changed=False,
                    source=surfaced_alert,
                )
            )

    @Agent.on("health.snapshot", model=HealthSnapshot)
    async def handle_snapshot(
        self,
        _message: AgentMessage,
        snapshot: HealthSnapshot,
    ) -> None:
        if (
            self._alert_policy_agent is None
            or snapshot.asset.asset_kind
            not in {AssetKind.LINUX_HOST, AssetKind.WINDOWS_HOST}
        ):
            await self._publish(self.repository.process_snapshot(snapshot))
            return
        bound_snapshot = self.repository.bind_snapshot(snapshot)
        outcome = await self._alert_policy_agent.process_host_snapshot(
            bound_snapshot.snapshot
        )
        health_changed = self.repository.record_snapshot_health(
            outcome.authoritative_snapshot
        )
        await self._publish(
            PortfolioPublish(
                asset=outcome.authoritative_snapshot.asset,
                asset_upserted=bound_snapshot.asset_upserted,
                health_changed=health_changed,
                source=outcome.authoritative_snapshot,
            )
        )
        for surfaced_alert in outcome.surfaced_alerts:
            await self._publish(
                PortfolioPublish(
                    asset=surfaced_alert.asset,
                    asset_upserted=False,
                    health_changed=False,
                    source=surfaced_alert,
                )
            )

    async def _publish(self, publish_request: PortfolioPublish) -> None:
        await self.send(
            OPS_BRIDGE_AGENT_ID,
            "portfolio.publish",
            publish_request.model_dump(mode="json"),
        )


__all__ = ["InventoryAgent"]
