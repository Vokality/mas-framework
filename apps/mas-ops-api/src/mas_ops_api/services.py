"""Shared application services for the ops-plane API."""

from __future__ import annotations

from dataclasses import dataclass

from mas_ops_api.auth.service import AuthService
from mas_ops_api.chat import ChatExecutionService, ChatService, PortfolioAssistant
from mas_ops_api.config.service import ConfigService
from mas_ops_api.connectors import ConnectorRegistry, PortfolioIngressRegistry
from mas_ops_api.db.session import Database
from mas_ops_api.incidents import IncidentProjectionService
from mas_ops_api.projections.portfolio_ingest import PortfolioIngestService
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService
from mas_msp_ai import DurableTaskRunner


@dataclass(slots=True)
class OpsApiServices:
    """Shared application services attached to FastAPI state."""

    settings: OpsApiSettings
    database: Database
    auth_service: AuthService
    chat_service: ChatService
    chat_execution_service: ChatExecutionService
    config_service: ConfigService
    command_connector_registry: ConnectorRegistry
    durable_task_runner: DurableTaskRunner
    incident_projection_service: IncidentProjectionService
    portfolio_assistant: PortfolioAssistant
    portfolio_ingest_service: PortfolioIngestService
    portfolio_ingress_registry: PortfolioIngressRegistry
    stream_service: StreamService


__all__ = ["OpsApiServices"]
