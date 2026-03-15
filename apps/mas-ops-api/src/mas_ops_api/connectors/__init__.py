"""Per-client fabric connectors for the ops plane."""

from .fabric_connector import OpsPlaneFabricConnector
from .in_process import InProcessFabricConnector
from .portfolio_ingress import (
    NullPortfolioIngressConnector,
    PortfolioIngressConnector,
    PortfolioIngressRegistry,
)
from .protocol import FabricConnector
from .registry import ConnectorRegistry, NullFabricConnector

__all__ = [
    "ConnectorRegistry",
    "FabricConnector",
    "InProcessFabricConnector",
    "NullFabricConnector",
    "NullPortfolioIngressConnector",
    "OpsPlaneFabricConnector",
    "PortfolioIngressConnector",
    "PortfolioIngressRegistry",
]
