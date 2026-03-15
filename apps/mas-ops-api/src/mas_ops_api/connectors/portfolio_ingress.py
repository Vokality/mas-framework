"""Read-only per-client ingress connectors for visibility events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mas_msp_contracts import PortfolioEvent


class PortfolioIngressConnector(Protocol):
    """Read-only connector interface for normalized portfolio visibility."""

    async def ingest_portfolio_event(self, *, event: PortfolioEvent) -> None: ...


class NullPortfolioIngressConnector:
    """Fallback ingress connector used when no client-specific ingress exists."""

    async def ingest_portfolio_event(self, *, event: PortfolioEvent) -> None:
        return None


class PortfolioIngressRegistry:
    """Resolve one read-only ingress connector per client."""

    def __init__(
        self,
        *,
        factory: Callable[[str], PortfolioIngressConnector] | None = None,
    ) -> None:
        self._default = NullPortfolioIngressConnector()
        self._factory = factory
        self._connectors: dict[str, PortfolioIngressConnector] = {}

    def register(self, client_id: str, connector: PortfolioIngressConnector) -> None:
        """Register an ingress connector for one client."""

        self._connectors[client_id] = connector

    def get(self, client_id: str) -> PortfolioIngressConnector:
        """Return the ingress connector for one client."""

        connector = self._connectors.get(client_id)
        if connector is not None:
            return connector
        if self._factory is None:
            return self._default
        connector = self._factory(client_id)
        self._connectors[client_id] = connector
        return connector


__all__ = [
    "NullPortfolioIngressConnector",
    "PortfolioIngressConnector",
    "PortfolioIngressRegistry",
]
