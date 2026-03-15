"""Shared application services for the ops-plane API."""

from __future__ import annotations

from dataclasses import dataclass

from mas_ops_api.auth.service import AuthService
from mas_ops_api.chat.service import ChatService
from mas_ops_api.config.service import ConfigService
from mas_ops_api.connectors import ConnectorRegistry, PortfolioIngressRegistry
from mas_ops_api.db.session import Database
from mas_ops_api.projections import PortfolioIngestService
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService


@dataclass(slots=True)
class OpsApiServices:
    """Shared application services attached to FastAPI state."""

    settings: OpsApiSettings
    database: Database
    auth_service: AuthService
    chat_service: ChatService
    config_service: ConfigService
    command_connector_registry: ConnectorRegistry
    portfolio_ingest_service: PortfolioIngestService
    portfolio_ingress_registry: PortfolioIngressRegistry
    stream_service: StreamService


__all__ = ["OpsApiServices"]
