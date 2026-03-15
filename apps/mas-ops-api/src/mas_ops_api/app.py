"""FastAPI application factory for the MSP ops-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from starlette.types import ASGIApp

from mas_ops_api.api import api_router
from mas_ops_api.audit import AuditService
from mas_ops_api.approvals import (
    ApprovalActionRouter,
    ApprovalService,
    ConfigApplyActionHandler,
    HostRemediationActionHandler,
    IncidentRemediationActionHandler,
    OpsPlaneApprovalStore,
)
from mas_ops_api.auth.passwords import PasswordService
from mas_ops_api.auth.service import AuthService
from mas_ops_api.chat import ChatExecutionService, ChatService, PortfolioAssistant
from mas_ops_api.config import (
    ConfigRunService,
    ConfigService,
    DesiredStateService,
    OpsPlaneConfigRunStore,
    OpsPlaneDesiredStateStore,
)
from mas_ops_api.connectors import (
    ConnectorRegistry,
    InProcessFabricConnector,
    OpsPlaneFabricConnector,
    PortfolioIngressRegistry,
)
from mas_ops_api.db.bootstrap import create_schema
from mas_ops_api.db.session import Database
from mas_ops_api.dogfood import DockerDogfoodMonitorService
from mas_ops_api.incidents import IncidentProjectionService, OpsPlaneIncidentGateway
from mas_ops_api.projections.portfolio_ingest import PortfolioIngestService
from mas_ops_api.services import OpsApiServices
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService
from mas_ops_api.visibility import (
    InProcessVisibilityRuntime,
    OpsPlaneAlertConditionStore,
    OpsPlaneAppliedAlertPolicyStore,
)
from mas_msp_ai import (
    AssetKindDiagnosticsExecutor,
    AssetKindRemediationExecutor,
    CoreOrchestratorAgent,
    DurableTaskRunner,
    FabricIncidentHandler,
    SummaryComposer,
)
from mas_msp_core import (
    AlertPolicyAgent,
    ApprovalController,
    ConfigDeployerAgent,
    NotificationService,
    NotifierTransportAgent,
    OpsBridgeAgent,
)
from mas_msp_hosts import (
    DockerLinuxDiagnosticsBackend,
    HostServiceRegistry,
    LinuxDiagnosticsAgent,
    LinuxExecutorAgent,
    WindowsDiagnosticsAgent,
    WindowsExecutorAgent,
)
from mas_msp_network import NetworkDiagnosticsAgent


def build_cors_middleware(
    app: ASGIApp,
    /,
    *,
    allow_origins: list[str],
    allow_credentials: bool,
    allow_methods: list[str],
    allow_headers: list[str],
) -> ASGIApp:
    """Adapt Starlette's CORS middleware to a fully typed middleware factory."""

    return CORSMiddleware(
        app,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )


def create_app(settings: OpsApiSettings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    app_settings = settings or OpsApiSettings()
    database = Database(app_settings)
    password_service = PasswordService()
    stream_service = StreamService(app_settings)
    audit_service = AuditService()
    durable_task_runner = DurableTaskRunner()
    summary_composer = SummaryComposer()
    core_orchestrator = CoreOrchestratorAgent(summary_composer=summary_composer)
    host_service_registry = HostServiceRegistry()
    network_diagnostics_agent = NetworkDiagnosticsAgent()
    linux_diagnostics_agent = LinuxDiagnosticsAgent(
        backend=(
            DockerLinuxDiagnosticsBackend()
            if app_settings.linux_diagnostics_backend == "docker"
            else None
        ),
        service_registry=(
            host_service_registry
            if app_settings.linux_diagnostics_backend == "registry"
            else None
        ),
    )
    linux_executor_agent = LinuxExecutorAgent(service_registry=host_service_registry)
    windows_diagnostics_agent = WindowsDiagnosticsAgent(
        service_registry=host_service_registry
    )
    windows_executor_agent = WindowsExecutorAgent(
        service_registry=host_service_registry
    )
    diagnostics_executor = AssetKindDiagnosticsExecutor(
        network_executor=network_diagnostics_agent,
        linux_executor=linux_diagnostics_agent,
        windows_executor=windows_diagnostics_agent,
    )
    remediation_executor = AssetKindRemediationExecutor(
        linux_executor=linux_executor_agent,
        windows_executor=windows_executor_agent,
    )
    incident_projection_service = IncidentProjectionService(stream_service)
    chat_service = ChatService(stream_service)
    applied_alert_policy_store = OpsPlaneAppliedAlertPolicyStore(database=database)
    alert_condition_store = OpsPlaneAlertConditionStore(database=database)
    incident_gateway = OpsPlaneIncidentGateway(
        database=database,
        incident_projection_service=incident_projection_service,
        stream_service=stream_service,
    )
    notification_service = NotificationService(
        applied_policy_store=applied_alert_policy_store,
    )
    notifier_transport_agent = NotifierTransportAgent(
        incident_context_reader=incident_gateway,
        transport=incident_gateway,
        notification_service=notification_service,
    )
    alert_policy_agent = AlertPolicyAgent(
        applied_policy_store=applied_alert_policy_store,
        condition_store=alert_condition_store,
        notifier=notifier_transport_agent,
    )
    approval_action_router = ApprovalActionRouter()
    approval_controller = ApprovalController(
        store=OpsPlaneApprovalStore(
            database=database,
            audit_service=audit_service,
            stream_service=stream_service,
        ),
        outcome_handler=approval_action_router,
    )
    approval_service = ApprovalService(
        approval_controller=approval_controller,
        expiry_poll_seconds=app_settings.approval_expiry_poll_seconds,
    )
    config_deployer = ConfigDeployerAgent(
        desired_state_store=OpsPlaneDesiredStateStore(database=database),
        run_store=OpsPlaneConfigRunStore(
            database=database,
            audit_service=audit_service,
            stream_service=stream_service,
        ),
        approval_controller=approval_controller,
        applied_policy_store=applied_alert_policy_store,
    )
    approval_action_router.register(
        "network.remediation",
        IncidentRemediationActionHandler(
            database=database,
            chat_service=chat_service,
            incident_projection_service=incident_projection_service,
            stream_service=stream_service,
        ),
    )
    approval_action_router.register(
        "config.apply",
        ConfigApplyActionHandler(config_deployer=config_deployer),
    )
    fabric_incident_handler = FabricIncidentHandler(
        incident_context_reader=incident_gateway,
        approval_controller=approval_controller,
        notifier=notifier_transport_agent,
        orchestrator=core_orchestrator,
        diagnostics_executor=diagnostics_executor,
        remediation_executor=remediation_executor,
    )
    ops_bridge_agent = OpsBridgeAgent(
        incident_chat_handler=fabric_incident_handler,
        visibility_alert_handler=fabric_incident_handler,
        incident_remediation_handler=fabric_incident_handler,
        approval_controller=approval_controller,
        config_deployer=config_deployer,
    )
    command_connector_registry = ConnectorRegistry(
        factory=lambda client_id: InProcessFabricConnector(
            client_id=client_id,
            bridge=ops_bridge_agent,
        )
    )
    approval_action_router.register(
        "host.remediation",
        HostRemediationActionHandler(
            command_connector_registry=command_connector_registry,
            database=database,
            chat_service=chat_service,
            incident_projection_service=incident_projection_service,
            stream_service=stream_service,
        ),
    )

    portfolio_ingest_service = PortfolioIngestService(
        database,
        stream_service,
    )
    portfolio_ingress_registry = PortfolioIngressRegistry(
        factory=lambda client_id: OpsPlaneFabricConnector(
            client_id=client_id,
            portfolio_ingest_service=portfolio_ingest_service,
        )
    )
    visibility_runtime = InProcessVisibilityRuntime(
        alert_policy_agent=alert_policy_agent,
        bridge=ops_bridge_agent,
        portfolio_ingress_registry=portfolio_ingress_registry,
    )
    portfolio_assistant = PortfolioAssistant()
    chat_execution_service = ChatExecutionService(
        database=database,
        chat_service=chat_service,
        portfolio_assistant=portfolio_assistant,
        command_connector_registry=command_connector_registry,
        stream_service=stream_service,
        task_runner=durable_task_runner,
    )
    middleware: list[Middleware] = []
    if app_settings.cors_allowed_origins:
        middleware.append(
            Middleware(
                build_cors_middleware,  # type: ignore[invalid-argument-type] ty does not recognize Starlette middleware factories that return ASGIApp.
                allow_origins=app_settings.cors_allowed_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        )
    services = OpsApiServices(
        settings=app_settings,
        approval_service=approval_service,
        database=database,
        audit_service=audit_service,
        auth_service=AuthService(app_settings, password_service=password_service),
        chat_service=chat_service,
        chat_execution_service=chat_execution_service,
        config_service=ConfigService(
            desired_state_service=DesiredStateService(
                audit_service=audit_service,
                stream_service=stream_service,
            ),
            run_service=ConfigRunService(
                audit_service=audit_service,
                stream_service=stream_service,
            ),
        ),
        command_connector_registry=command_connector_registry,
        durable_task_runner=durable_task_runner,
        incident_projection_service=incident_projection_service,
        portfolio_assistant=portfolio_assistant,
        portfolio_ingress_registry=portfolio_ingress_registry,
        portfolio_ingest_service=portfolio_ingest_service,
        stream_service=stream_service,
        visibility_runtime=visibility_runtime,
    )
    dogfood_monitor_service = DockerDogfoodMonitorService(
        settings=app_settings,
        visibility_runtime=visibility_runtime,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services
        if app_settings.auto_create_schema:
            await create_schema(database.engine)
        approval_service.start()
        dogfood_monitor_service.start()
        yield
        await dogfood_monitor_service.stop()
        await approval_service.stop()
        await durable_task_runner.drain()
        await database.dispose()

    app = FastAPI(
        title="MAS Ops API",
        version="0.4.5",
        lifespan=lifespan,
        middleware=middleware,
    )
    app.include_router(api_router)
    return app


__all__ = ["OpsApiServices", "create_app"]
