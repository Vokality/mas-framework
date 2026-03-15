"""Read-only per-client connector for Phase 2 portfolio visibility."""

from __future__ import annotations

from mas_msp_contracts import PortfolioEvent
from mas_msp_core import build_ops_plane_connector_id

from mas_ops_api.projections.portfolio_ingest import PortfolioIngestService


class OpsPlaneFabricConnector:
    """Accept normalized portfolio events for one client connector id."""

    def __init__(
        self,
        *,
        client_id: str,
        portfolio_ingest_service: PortfolioIngestService,
    ) -> None:
        self.client_id = client_id
        self.connector_id = build_ops_plane_connector_id(client_id)
        self._portfolio_ingest_service = portfolio_ingest_service

    async def ingest_portfolio_event(self, *, event: PortfolioEvent) -> None:
        """Consume one portfolio event for the matching client connector."""

        if event.client_id != self.client_id:
            raise ValueError("portfolio event client_id must match connector client_id")
        await self._portfolio_ingest_service.ingest_event(event)


__all__ = ["OpsPlaneFabricConnector"]
