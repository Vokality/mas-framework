"""Windows host event ingest agent."""

from __future__ import annotations

from uuid import uuid4

from mas_agent import Agent, TlsClientConfig
from mas_msp_contracts import AlertRaised, AssetKind, Severity

from mas_msp_core import INVENTORY_AGENT_ID, WINDOWS_EVENT_INGEST_AGENT_ID

from ..common import build_host_asset_ref
from .models import WindowsEventRecord


class WindowsEventIngestAgent(Agent[dict[str, object]]):
    """Normalize Windows host events and forward them to inventory."""

    def __init__(
        self,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            WINDOWS_EVENT_INGEST_AGENT_ID,
            capabilities=["msp:hosts:windows:ingest"],
            server_addr=server_addr,
            tls=tls,
        )

    def normalize_wef_event(self, event: WindowsEventRecord) -> AlertRaised:
        """Normalize one Windows host event into a shared alert contract."""

        hostname = event.hostname or event.mgmt_address or "windows-host"
        category = "service" if event.service_name else "event_log"
        title = (
            f"{event.service_name} on {hostname}: {event.message}"
            if event.service_name
            else f"Windows event on {hostname}: {event.message}"
        )
        return AlertRaised(
            alert_id=str(uuid4()),
            client_id=event.client_id,
            fabric_id=event.fabric_id,
            asset=build_host_asset_ref(
                asset_kind=AssetKind.WINDOWS_HOST,
                client_id=event.client_id,
                fabric_id=event.fabric_id,
                vendor="Windows",
                model=event.edition,
                hostname=event.hostname,
                mgmt_address=event.mgmt_address,
                site=event.site,
                tags=[*event.tags, "windows"],
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

    async def ingest_wef_event(self, event: WindowsEventRecord) -> AlertRaised:
        """Normalize and forward one Windows host event."""

        alert = self.normalize_wef_event(event)
        await self.send(
            INVENTORY_AGENT_ID, "alert.raised", alert.model_dump(mode="json")
        )
        return alert


def _map_level(level: str) -> Severity:
    normalized = level.strip().lower()
    if normalized in {"critical", "error"}:
        return Severity.CRITICAL if normalized == "critical" else Severity.MAJOR
    if normalized in {"warning", "warn"}:
        return Severity.WARNING
    return Severity.INFO


__all__ = ["WindowsEventIngestAgent"]
