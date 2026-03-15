"""Inventory agent for deterministic asset binding."""

from __future__ import annotations

from mas_agent import Agent, AgentMessage, TlsClientConfig
from mas_msp_contracts import AlertRaised, HealthSnapshot

from mas_msp_core.agent_ids import INVENTORY_AGENT_ID, OPS_BRIDGE_AGENT_ID
from mas_msp_core.inventory.repository import InventoryRepository


class InventoryAgent(Agent[dict[str, object]]):
    """Bind visibility payloads to stable assets and publish bridge messages."""

    def __init__(
        self,
        *,
        repository: InventoryRepository | None = None,
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

    @Agent.on("alert.raised", model=AlertRaised)
    async def handle_alert(
        self,
        _message: AgentMessage,
        alert: AlertRaised,
    ) -> None:
        publish_request = self.repository.process_alert(alert)
        await self.send(
            OPS_BRIDGE_AGENT_ID,
            "portfolio.publish",
            publish_request.model_dump(mode="json"),
        )

    @Agent.on("health.snapshot", model=HealthSnapshot)
    async def handle_snapshot(
        self,
        _message: AgentMessage,
        snapshot: HealthSnapshot,
    ) -> None:
        publish_request = self.repository.process_snapshot(snapshot)
        await self.send(
            OPS_BRIDGE_AGENT_ID,
            "portfolio.publish",
            publish_request.model_dump(mode="json"),
        )


__all__ = ["InventoryAgent"]
