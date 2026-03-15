"""Linux host event ingest agent."""

from __future__ import annotations

from uuid import uuid4

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import AlertRaised, AssetKind, Severity

from mas_msp_core import INVENTORY_AGENT_ID, LINUX_EVENT_INGEST_AGENT_ID

from ..common import build_host_asset_ref
from .models import LinuxJournalEvent


class LinuxEventIngestAgent(Agent[dict[str, object]]):
    """Normalize Linux host events and forward them to inventory."""

    def __init__(
        self,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            LINUX_EVENT_INGEST_AGENT_ID,
            capabilities=["msp:hosts:linux:ingest"],
            server_addr=server_addr,
            tls=tls,
        )

    def normalize_journal_event(self, event: LinuxJournalEvent) -> AlertRaised:
        """Normalize one Linux host event into a shared alert contract."""

        hostname = event.hostname or event.mgmt_address or "linux-host"
        category = "service" if event.service_name else "system"
        title = (
            f"{event.service_name} on {hostname}: {event.message}"
            if event.service_name
            else f"Linux host event on {hostname}: {event.message}"
        )
        return AlertRaised(
            alert_id=str(uuid4()),
            client_id=event.client_id,
            fabric_id=event.fabric_id,
            asset=build_host_asset_ref(
                asset_kind=AssetKind.LINUX_HOST,
                client_id=event.client_id,
                fabric_id=event.fabric_id,
                vendor="Linux",
                model=event.distribution,
                hostname=event.hostname,
                mgmt_address=event.mgmt_address,
                site=event.site,
                tags=[*event.tags, "linux"],
            ),
            source_kind=event.source_kind,
            occurred_at=event.occurred_at,
            severity=_map_level(event.level),
            category=category,
            title=title,
            normalized_facts={
                "message": event.message,
                "service_name": event.service_name,
            },
        )

    async def ingest_journal_event(self, event: LinuxJournalEvent) -> AlertRaised:
        """Normalize and forward one Linux host event."""

        alert = self.normalize_journal_event(event)
        await self.send(
            INVENTORY_AGENT_ID, "alert.raised", alert.model_dump(mode="json")
        )
        return alert


def _map_level(level: str) -> Severity:
    normalized = level.strip().lower()
    if normalized in {"critical", "crit", "alert", "emerg"}:
        return Severity.CRITICAL
    if normalized in {"error", "err"}:
        return Severity.MAJOR
    if normalized in {"warning", "warn"}:
        return Severity.WARNING
    return Severity.INFO


__all__ = ["LinuxEventIngestAgent"]
