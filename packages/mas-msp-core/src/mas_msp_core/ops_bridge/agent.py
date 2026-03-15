"""Ops bridge agent for Phase 2 portfolio event emission."""

from __future__ import annotations

from datetime import UTC
from uuid import uuid4

from mas_agent import Agent, AgentMessage, TlsClientConfig
from mas_msp_contracts import AlertRaised, HealthSnapshot, PortfolioEvent

from mas_msp_core.agent_ids import OPS_BRIDGE_AGENT_ID, build_ops_plane_connector_id
from mas_msp_core.messages import PortfolioPublish


class OpsBridgeAgent(Agent[dict[str, object]]):
    """Convert resolved visibility payloads into ops-plane portfolio events."""

    def __init__(
        self,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(
            OPS_BRIDGE_AGENT_ID,
            capabilities=["msp:portfolio:bridge"],
            server_addr=server_addr,
            tls=tls,
        )

    @Agent.on("portfolio.publish", model=PortfolioPublish)
    async def handle_portfolio_publish(
        self,
        _message: AgentMessage,
        publish_request: PortfolioPublish,
    ) -> None:
        connector_id = build_ops_plane_connector_id(publish_request.asset.client_id)
        for event in self.build_portfolio_events(publish_request):
            await self.send(
                connector_id,
                "portfolio.event",
                event.model_dump(mode="json"),
            )

    def build_portfolio_events(
        self,
        publish_request: PortfolioPublish,
    ) -> list[PortfolioEvent]:
        """Return the additive portfolio events created from one visibility update."""

        source = publish_request.source
        if source.client_id != publish_request.asset.client_id:
            raise ValueError(
                "publish request client_id must match resolved asset client_id"
            )
        if source.fabric_id != publish_request.asset.fabric_id:
            raise ValueError(
                "publish request fabric_id must match resolved asset fabric_id"
            )

        events: list[PortfolioEvent] = []
        if publish_request.asset_upserted:
            events.append(
                self._build_event(
                    client_id=publish_request.asset.client_id,
                    fabric_id=publish_request.asset.fabric_id,
                    event_type="asset.upserted",
                    subject_type="asset",
                    subject_id=publish_request.asset.asset_id,
                    occurred_at=self._event_time(source),
                    payload={
                        "asset": publish_request.asset.model_dump(mode="json"),
                        "source_reference": self._source_reference(source),
                    },
                )
            )

        if isinstance(source, AlertRaised):
            events.append(
                self._build_event(
                    client_id=source.client_id,
                    fabric_id=source.fabric_id,
                    event_type="network.alert.raised",
                    subject_type="alert",
                    subject_id=source.alert_id,
                    occurred_at=source.occurred_at,
                    payload={"alert": source.model_dump(mode="json")},
                )
            )
            return events

        if publish_request.health_changed:
            events.append(
                self._build_event(
                    client_id=source.client_id,
                    fabric_id=source.fabric_id,
                    event_type="asset.health.changed",
                    subject_type="asset",
                    subject_id=publish_request.asset.asset_id,
                    occurred_at=source.collected_at,
                    payload={
                        "asset": publish_request.asset.model_dump(mode="json"),
                        "snapshot": source.model_dump(mode="json"),
                    },
                )
            )
        events.append(
            self._build_event(
                client_id=source.client_id,
                fabric_id=source.fabric_id,
                event_type="network.snapshot.recorded",
                subject_type="snapshot",
                subject_id=source.snapshot_id,
                occurred_at=source.collected_at,
                payload={"snapshot": source.model_dump(mode="json")},
            )
        )
        return events

    @staticmethod
    def _build_event(
        *,
        client_id: str,
        fabric_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        occurred_at,
        payload: dict[str, object],
    ) -> PortfolioEvent:
        return PortfolioEvent(
            event_id=str(uuid4()),
            client_id=client_id,
            fabric_id=fabric_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            occurred_at=occurred_at.astimezone(UTC),
            payload_version=1,
            payload=payload,
        )

    @staticmethod
    def _event_time(source: AlertRaised | HealthSnapshot):
        if isinstance(source, AlertRaised):
            return source.occurred_at
        return source.collected_at

    @staticmethod
    def _source_reference(source: AlertRaised | HealthSnapshot) -> dict[str, str]:
        if isinstance(source, AlertRaised):
            return {"source_type": "alert", "source_id": source.alert_id}
        return {"source_type": "snapshot", "source_id": source.snapshot_id}


__all__ = ["OpsBridgeAgent"]
