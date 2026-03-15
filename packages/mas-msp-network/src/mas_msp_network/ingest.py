"""Network event ingest agent for syslog and SNMP traps."""

from __future__ import annotations

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import AlertRaised

from mas_msp_core import INVENTORY_AGENT_ID, NETWORK_EVENT_INGEST_AGENT_ID
from mas_msp_network.models import SnmpTrapEvent, SyslogEvent
from mas_msp_network.vendors import cisco, fortigate


class NetworkEventIngestAgent(Agent[dict[str, object]]):
    """Normalize vendor events into Phase 0 alerts and forward them to inventory."""

    def __init__(
        self,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            NETWORK_EVENT_INGEST_AGENT_ID,
            capabilities=["msp:network:ingest"],
            server_addr=server_addr,
            tls=tls,
        )

    def normalize_syslog(self, event: SyslogEvent) -> AlertRaised:
        """Normalize one syslog event into an alert contract."""

        return _vendor_module(event.vendor).normalize_syslog(event)

    def normalize_snmp_trap(self, event: SnmpTrapEvent) -> AlertRaised:
        """Normalize one SNMP trap event into an alert contract."""

        return _vendor_module(event.vendor).normalize_snmp_trap(event)

    async def ingest_syslog(self, event: SyslogEvent) -> AlertRaised:
        """Normalize and forward one syslog message."""

        alert = self.normalize_syslog(event)
        await self.send(
            INVENTORY_AGENT_ID,
            "alert.raised",
            alert.model_dump(mode="json"),
        )
        return alert

    async def ingest_snmp_trap(self, event: SnmpTrapEvent) -> AlertRaised:
        """Normalize and forward one SNMP trap."""

        alert = self.normalize_snmp_trap(event)
        await self.send(
            INVENTORY_AGENT_ID,
            "alert.raised",
            alert.model_dump(mode="json"),
        )
        return alert


def _vendor_module(vendor: str):
    normalized = vendor.strip().lower()
    if normalized.startswith("cisco"):
        return cisco
    if normalized in {"fortigate", "fortinet"}:
        return fortigate
    raise ValueError(f"unsupported vendor: {vendor}")


__all__ = ["NetworkEventIngestAgent"]
