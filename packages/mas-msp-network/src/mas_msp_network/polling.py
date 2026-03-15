"""Network polling agent for SNMPv3 visibility snapshots."""

from __future__ import annotations

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import HealthSnapshot

from mas_msp_core import INVENTORY_AGENT_ID, NETWORK_POLLING_AGENT_ID
from mas_msp_network.models import SnmpPollObservation, SnmpV3Poller, SnmpV3Target
from mas_msp_network.vendors import cisco, fortigate


class NetworkPollingAgent(Agent[dict[str, object]]):
    """Poll supported devices over SNMPv3 and emit health snapshots."""

    def __init__(
        self,
        *,
        poller: SnmpV3Poller,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            NETWORK_POLLING_AGENT_ID,
            capabilities=["msp:network:poll"],
            server_addr=server_addr,
            tls=tls,
        )
        self._poller = poller

    def normalize_poll(
        self,
        target: SnmpV3Target,
        observation: SnmpPollObservation,
    ) -> HealthSnapshot:
        """Normalize one vendor poll observation into a health snapshot."""

        return _vendor_module(target.vendor).normalize_snmp_poll(target, observation)

    async def poll_target(self, target: SnmpV3Target) -> HealthSnapshot:
        """Poll one target and forward the normalized health snapshot to inventory."""

        observation = await self._poller.poll(target)
        snapshot = self.normalize_poll(target, observation)
        await self.send(
            INVENTORY_AGENT_ID,
            "health.snapshot",
            snapshot.model_dump(mode="json"),
        )
        return snapshot


def _vendor_module(vendor: str):
    normalized = vendor.strip().lower()
    if normalized.startswith("cisco"):
        return cisco
    if normalized in {"fortigate", "fortinet"}:
        return fortigate
    raise ValueError(f"unsupported vendor: {vendor}")


__all__ = ["NetworkPollingAgent"]
