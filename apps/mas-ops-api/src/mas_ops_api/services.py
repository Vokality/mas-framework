"""Shared application services for the ops-plane API."""

from __future__ import annotations

from dataclasses import dataclass

from mas_ops_api.approvals import ApprovalService
from mas_ops_api.audit import AuditService
from mas_ops_api.auth.service import AuthService
from mas_ops_api.chat import ChatExecutionService, ChatService, PortfolioAssistant
from mas_ops_api.config.service import ConfigService
from mas_ops_api.connectors import ConnectorRegistry, PortfolioIngressRegistry
from mas_ops_api.db.session import Database
from mas_ops_api.incidents import IncidentProjectionService
from mas_ops_api.projections.portfolio_ingest import PortfolioIngestService
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService
from mas_ops_api.visibility import InProcessVisibilityRuntime
from mas_msp_ai import DurableTaskRunner


@dataclass(slots=True)
class OpsApiReadiness:
    """Startup/readiness state exposed to container liveness checks."""

    ready: bool = False
    detail: str = "startup_in_progress"

    def mark_ready(self) -> None:
        """Mark the app ready for health checks."""

        self.ready = True
        self.detail = "ok"

    def mark_not_ready(self, detail: str) -> None:
        """Mark the app not ready and record the reason."""

        self.ready = False
        self.detail = detail


@dataclass(slots=True)
class OpsApiServices:
    """Shared application services attached to FastAPI state."""

    settings: OpsApiSettings
    readiness: OpsApiReadiness
    approval_service: ApprovalService
    database: Database
    audit_service: AuditService
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
    visibility_runtime: InProcessVisibilityRuntime


__all__ = ["OpsApiReadiness", "OpsApiServices"]
