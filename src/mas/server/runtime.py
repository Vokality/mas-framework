"""MAS server runtime composition root."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import grpc.aio as grpc_aio

from .._proto.v1 import mas_pb2_grpc
from ..gateway.audit import AuditFileSink, AuditModule
from ..gateway.authorization import AuthorizationModule
from ..gateway.circuit_breaker import CircuitBreakerConfig, CircuitBreakerModule
from ..gateway.config import GatewaySettings, RedisSettings
from ..gateway.dlp import DLPModule
from ..gateway.rate_limit import RateLimitModule
from ..redis_client import create_redis_client
from ..redis_types import AsyncRedisProtocol
from ..telemetry import SpanKind, TelemetryConfig, configure_telemetry, get_telemetry
from .delivery import DeliveryService
from .ingress import IngressService
from .registry import RegistryService
from .routing import MessageRouter
from .servicer import MasGrpcServicer
from .sessions import SessionManager
from .state import StateStore
from .tls import load_server_credentials
from .types import MASServerSettings, Session

logger = logging.getLogger(__name__)


class MASServer:
    """MAS server (gRPC + mTLS) that owns all Redis responsibilities."""

    def __init__(
        self,
        *,
        settings: MASServerSettings,
        gateway: GatewaySettings | None = None,
    ) -> None:
        """Initialize server state and gateway settings."""
        self._settings = settings
        self._gateway_settings = gateway or GatewaySettings(
            redis=RedisSettings(url=settings.redis_url)
        )

        self._redis: AsyncRedisProtocol | None = None
        self._grpc_server: grpc_aio.Server | None = None
        self._bound_addr: str | None = None
        self._running = False

        self._audit: AuditModule | None = None
        self._authz: AuthorizationModule | None = None
        self._rate_limit: RateLimitModule | None = None
        self._dlp: DLPModule | None = None
        self._circuit_breaker: CircuitBreakerModule | None = None

        self._sessions: SessionManager | None = None
        self._registry: RegistryService | None = None
        self._state_store: StateStore | None = None
        self._router: MessageRouter | None = None
        self._delivery: DeliveryService | None = None
        self._ingress: IngressService | None = None

    async def start(self) -> None:
        """Start Redis connection, modules, and gRPC server."""
        telemetry = configure_telemetry(
            TelemetryConfig(
                enabled=self._gateway_settings.telemetry.enabled,
                service_name=self._gateway_settings.telemetry.service_name,
                service_namespace=self._gateway_settings.telemetry.service_namespace,
                environment=self._gateway_settings.telemetry.environment,
                otlp_endpoint=self._gateway_settings.telemetry.otlp_endpoint,
                sample_ratio=self._gateway_settings.telemetry.sample_ratio,
                export_metrics=self._gateway_settings.telemetry.export_metrics,
                metrics_export_interval_ms=self._gateway_settings.telemetry.metrics_export_interval_ms,
                headers=dict(self._gateway_settings.telemetry.headers),
            )
        )
        with telemetry.start_span("mas.server.start", kind=SpanKind.INTERNAL):
            await self._start_runtime()

    async def _start_runtime(self) -> None:
        """Start runtime internals after telemetry setup."""
        redis_conn = create_redis_client(
            url=self._gateway_settings.redis.url,
            decode_responses=self._gateway_settings.redis.decode_responses,
            socket_timeout=self._gateway_settings.redis.socket_timeout,
        )
        self._redis = redis_conn

        audit_settings = self._gateway_settings.audit
        file_sink: AuditFileSink | None = None
        if audit_settings.file_path:
            file_sink = AuditFileSink(
                audit_settings.file_path,
                max_bytes=audit_settings.max_bytes,
                backup_count=audit_settings.backup_count,
            )
        self._audit = AuditModule(redis_conn, file_sink=file_sink)
        self._authz = AuthorizationModule(
            redis_conn, enable_rbac=self._gateway_settings.features.rbac
        )
        self._rate_limit = RateLimitModule(
            redis_conn,
            default_per_minute=self._gateway_settings.rate_limit.per_minute,
            default_per_hour=self._gateway_settings.rate_limit.per_hour,
        )

        if self._gateway_settings.features.dlp:
            dlp_settings = self._gateway_settings.dlp
            self._dlp = DLPModule(
                custom_policies=dlp_settings.policy_overrides,
                custom_rules=dlp_settings.rules,
                merge_strategy=dlp_settings.merge_strategy,
                disable_defaults=dlp_settings.disable_defaults,
            )

        if self._gateway_settings.features.circuit_breaker:
            cb_config = CircuitBreakerConfig(
                failure_threshold=self._gateway_settings.circuit_breaker.failure_threshold,
                success_threshold=self._gateway_settings.circuit_breaker.success_threshold,
                timeout_seconds=self._gateway_settings.circuit_breaker.timeout_seconds,
                window_seconds=self._gateway_settings.circuit_breaker.window_seconds,
            )
            self._circuit_breaker = CircuitBreakerModule(redis_conn, config=cb_config)

        self._sessions = SessionManager(agents=self._settings.agents)
        self._registry = RegistryService(redis=redis_conn, agents=self._settings.agents)
        self._state_store = StateStore(redis_conn)
        self._router = MessageRouter(
            redis=redis_conn, dlq_enabled=self._audit is not None
        )
        self._delivery = DeliveryService(
            redis=redis_conn,
            settings=self._settings,
            sessions=self._sessions,
            router=self._router,
            circuit_breaker=self._circuit_breaker,
        )

        self._ingress = IngressService(
            sessions=self._sessions,
            policy=self._build_policy_pipeline(),
            redis=redis_conn,
        )

        await self._registry.bootstrap_registry()

        grpc_server = grpc_aio.server()
        mas_pb2_grpc.add_MasServiceServicer_to_server(
            MasGrpcServicer(self), grpc_server
        )

        creds = load_server_credentials(self._settings.tls)
        port = grpc_server.add_secure_port(self._settings.listen_addr, creds)
        if self._settings.listen_addr.endswith(":0"):
            host = self._settings.listen_addr.rsplit(":", 1)[0]
            self._bound_addr = f"{host}:{port}"
        else:
            self._bound_addr = self._settings.listen_addr

        await grpc_server.start()
        self._grpc_server = grpc_server

        self._running = True
        self._delivery.set_running(True)

        logger.info(
            "MAS server started",
            extra={
                "listen": self._bound_addr,
                "agents_allowlisted": len(self._settings.agents),
            },
        )

    async def stop(self) -> None:
        """Stop gRPC server, Redis connection, and sessions."""
        telemetry = get_telemetry()
        with telemetry.start_span("mas.server.stop", kind=SpanKind.INTERNAL):
            await self._stop_runtime()

    async def _stop_runtime(self) -> None:
        """Stop runtime internals."""
        self._running = False
        if self._delivery:
            self._delivery.set_running(False)

        sessions: list[Session] = []
        if self._sessions:
            sessions = await self._sessions.snapshot_and_clear()

        for session in sessions:
            session.task.cancel()
        await asyncio.gather(
            *(session.task for session in sessions), return_exceptions=True
        )

        if self._grpc_server is not None:
            await self._grpc_server.stop(grace=2.0)
            self._grpc_server = None
            self._bound_addr = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        get_telemetry().shutdown()

        logger.info("MAS server stopped")

    @property
    def authz(self) -> AuthorizationModule:
        """Return the authorization module after startup."""
        if self._authz is None:
            raise RuntimeError("Server not started")
        return self._authz

    @property
    def bound_addr(self) -> str:
        """Return bound listen address after startup."""
        if self._bound_addr is None:
            raise RuntimeError("Server not started")
        return self._bound_addr

    async def connect_session(
        self,
        *,
        agent_id: str,
        instance_id: str,
    ) -> Session:
        """Create a session for a connecting agent instance."""
        sessions = self._require_sessions()
        delivery = self._require_delivery()
        registry = self._require_registry()

        session = await sessions.connect(
            agent_id=agent_id,
            instance_id=instance_id,
            task_factory=delivery.start_stream_task,
        )
        get_telemetry().update_active_sessions(delta=1)
        await registry.set_agent_status(agent_id, "ACTIVE")
        return session

    async def disconnect_session(self, *, agent_id: str, instance_id: str) -> None:
        """Disconnect a session and update agent status."""
        sessions = self._require_sessions()
        registry = self._require_registry()

        session, remaining = await sessions.disconnect(
            agent_id=agent_id,
            instance_id=instance_id,
        )
        if session is not None:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
            get_telemetry().update_active_sessions(delta=-1)

        if not remaining:
            await registry.set_agent_status(agent_id, "INACTIVE")

    async def handle_ack(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
    ) -> None:
        """Handle delivery ACK and update inflight state."""
        await self._require_delivery().handle_ack(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
        )

    async def handle_nack(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
        reason: str,
        retryable: bool,
    ) -> None:
        """Handle delivery NACK and retry or DLQ."""
        await self._require_delivery().handle_nack(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
            reason=reason,
            retryable=retryable,
        )

    async def send_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        target_id: str,
        message_type: str,
        data_json: str,
    ) -> str:
        """Send a one-way message through policy checks and routing."""
        return await self._require_ingress().send_message(
            sender_id=sender_id,
            sender_instance_id=sender_instance_id,
            target_id=target_id,
            message_type=message_type,
            data_json=data_json,
        )

    async def request_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        target_id: str,
        message_type: str,
        data_json: str,
        timeout_ms: int,
    ) -> tuple[str, str]:
        """Send a request and register correlation tracking."""
        return await self._require_ingress().request_message(
            sender_id=sender_id,
            sender_instance_id=sender_instance_id,
            target_id=target_id,
            message_type=message_type,
            data_json=data_json,
            timeout_ms=timeout_ms,
        )

    async def reply_message(
        self,
        *,
        sender_id: str,
        sender_instance_id: str,
        correlation_id: str,
        message_type: str,
        data_json: str,
    ) -> str:
        """Send a reply to a pending request."""
        return await self._require_ingress().reply_message(
            sender_id=sender_id,
            sender_instance_id=sender_instance_id,
            correlation_id=correlation_id,
            message_type=message_type,
            data_json=data_json,
        )

    async def discover(
        self,
        *,
        agent_id: str,
        capabilities: list[str],
    ) -> list[dict[str, Any]]:
        """List discoverable agents for a sender and capability filter."""
        return await self._require_registry().discover(
            agent_id=agent_id,
            capabilities=capabilities,
        )

    async def get_state(self, *, agent_id: str) -> dict[str, str]:
        """Return persisted state for an agent."""
        return await self._require_state_store().get_state(agent_id=agent_id)

    async def update_state(self, *, agent_id: str, updates: dict[str, str]) -> None:
        """Update persisted agent state with provided fields."""
        await self._require_state_store().update_state(
            agent_id=agent_id, updates=updates
        )

    async def reset_state(self, *, agent_id: str) -> None:
        """Clear persisted agent state."""
        await self._require_state_store().reset_state(agent_id=agent_id)

    def _build_policy_pipeline(self):
        """Build policy pipeline from initialized modules."""
        from .policy import PolicyPipeline

        if (
            self._authz is None
            or self._rate_limit is None
            or self._audit is None
            or self._router is None
        ):
            raise RuntimeError("Server not started")

        return PolicyPipeline(
            authz=self._authz,
            rate_limit=self._rate_limit,
            audit=self._audit,
            router=self._router,
            dlp=self._dlp,
            circuit_breaker=self._circuit_breaker,
        )

    def _require_sessions(self) -> SessionManager:
        if self._sessions is None:
            raise RuntimeError("Server not started")
        return self._sessions

    def _require_registry(self) -> RegistryService:
        if self._registry is None:
            raise RuntimeError("Server not started")
        return self._registry

    def _require_state_store(self) -> StateStore:
        if self._state_store is None:
            raise RuntimeError("Server not started")
        return self._state_store

    def _require_delivery(self) -> DeliveryService:
        if self._delivery is None:
            raise RuntimeError("Server not started")
        return self._delivery

    def _require_ingress(self) -> IngressService:
        if self._ingress is None:
            raise RuntimeError("Server not started")
        return self._ingress
