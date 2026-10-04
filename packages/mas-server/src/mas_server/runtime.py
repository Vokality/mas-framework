"""MAS server runtime composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Concatenate
from uuid import uuid4

import grpc.aio as grpc_aio
from mas_core import (
    SpanKind,
    TelemetryConfig,
    configure_telemetry,
    create_redis_client,
    get_telemetry,
)
from mas_core.durability import RedisDurability
from mas_core.telemetry.observations import broker_scope
from mas_gateway import (
    AuditFileSink,
    AuditModule,
    AuthorizationModule,
    CircuitBreakerConfig,
    CircuitBreakerModule,
    DLPModule,
    GatewaySettings,
    RateLimitModule,
)
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from redis.asyncio import Redis

from .delivery import DeliveryService
from .ingress import IngressService
from .management import ManagementService
from .observation import BrokerObservationSupervisor
from .policy import PolicyPipeline
from .registry import RegistryService
from .routing import MessageRouter
from .servicer import MasGrpcServicer
from .sessions import SessionManager
from .state import StateSnapshot, StateStore
from .tls import load_server_credentials
from .types import AgentDiscoveryRecord, MASServerSettings, Session

logger = logging.getLogger(__name__)


def _broker_operation[**ArgsT, ResultT](
    operation: Callable[Concatenate[MASServer, ArgsT], Awaitable[ResultT]],
) -> Callable[Concatenate[MASServer, ArgsT], Awaitable[ResultT]]:
    """Attribute direct runtime operations and their child tasks to one broker."""

    @wraps(operation)
    async def scoped(
        server: MASServer, *args: ArgsT.args, **kwargs: ArgsT.kwargs
    ) -> ResultT:
        with broker_scope(server.broker_id):
            return await operation(server, *args, **kwargs)

    return scoped


class MASServer:
    """MAS server (gRPC + mTLS) that owns all Redis responsibilities."""

    def __init__(
        self,
        *,
        settings: MASServerSettings,
        gateway: GatewaySettings,
    ) -> None:
        """Initialize server state and gateway settings."""
        self._settings = settings
        self._gateway_settings = gateway
        self._broker_id = settings.broker_id or uuid4().hex

        self._redis: Redis | None = None
        self._grpc_server: grpc_aio.Server | None = None
        self._bound_addr: str | None = None
        self._running = False
        self._lifecycle_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._management: ManagementService | None = None
        self._observations: BrokerObservationSupervisor | None = None

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
        async with self._lifecycle_lock:
            if self._running:
                return
            if (
                self._management is not None
                or self._grpc_server is not None
                or self._redis is not None
                or self._observations is not None
            ):
                await self._finish_stop()
            try:
                await self._start_with_telemetry()
            except BaseException:
                await self._finish_stop()
                raise

    async def _start_with_telemetry(self) -> None:
        """Configure instrumentation and start the owned resources."""
        telemetry = await configure_telemetry(
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
        with (
            broker_scope(self._broker_id),
            telemetry.start_span("mas.server.start", kind=SpanKind.INTERNAL),
        ):
            await self._start_runtime()

    async def _start_runtime(self) -> None:
        """Start runtime internals after telemetry setup."""
        redis_conn = create_redis_client(
            url=self._gateway_settings.redis.url,
            socket_timeout=self._gateway_settings.redis.socket_timeout,
            sentinel=self._gateway_settings.redis.sentinel,
            pool=self._gateway_settings.redis.pool,
        )
        self._redis = redis_conn
        await redis_conn.ping()
        durability = RedisDurability(self._gateway_settings.redis.durability)

        audit_settings = self._gateway_settings.audit
        file_sink: AuditFileSink | None = None
        if audit_settings.file_path:
            file_sink = AuditFileSink(
                audit_settings.file_path,
                max_bytes=audit_settings.max_bytes,
                backup_count=audit_settings.backup_count,
            )
        self._audit = AuditModule(
            redis_conn,
            file_sink=file_sink,
            retention=audit_settings.retention,
            durability=durability,
        )
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

        self._sessions = SessionManager(
            agents=self._settings.agents,
            redis=redis_conn,
            lease_settings=self._settings.session_lease,
        )
        self._registry = RegistryService(redis=redis_conn, agents=self._settings.agents)
        self._state_store = StateStore(
            redis_conn, durability=self._gateway_settings.redis.durability
        )
        self._router = MessageRouter(
            redis=redis_conn,
            dlq_enabled=self._audit is not None,
            durability=durability,
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
        self._grpc_server = grpc_server
        mas_pb2_grpc.add_RuntimeServiceServicer_to_server(
            MasGrpcServicer(self), grpc_server
        )

        creds = await asyncio.to_thread(load_server_credentials, self._settings.tls)
        port = grpc_server.add_secure_port(self._settings.listen_addr, creds)
        if port == 0:
            raise RuntimeError("Unable to bind gRPC listener")
        if self._settings.listen_addr.endswith(":0"):
            host = self._settings.listen_addr.rsplit(":", 1)[0]
            self._bound_addr = f"{host}:{port}"
        else:
            self._bound_addr = self._settings.listen_addr

        await grpc_server.start()
        self._grpc_server = grpc_server

        self._running = True
        self._delivery.set_running(True)

        if self._settings.observations is not None:
            self._observations = BrokerObservationSupervisor(
                broker_id=self._broker_id,
                settings=self._settings.observations,
                redis=redis_conn,
                sessions=self._sessions,
                is_running=lambda: self._running,
                listen_addr=self.bound_addr,
            )
            await self._observations.start()

        if self._settings.management is not None:
            self._management = ManagementService(
                settings=self._settings.management,
                redis=redis_conn,
                sessions=self._sessions,
                agents=self._settings.agents,
                gateway=self._gateway_settings,
                is_running=lambda: self._running,
                audit=self._audit,
                circuit_breaker=self._circuit_breaker,
                broker_id=self._broker_id,
                observations=self._observations.store
                if self._observations is not None
                else None,
                observation_running=(
                    lambda: (
                        self._observations is not None and self._observations.running
                    )
                )
                if self._observations is not None
                else None,
            )
            await self._management.start()
            if self._observations is not None:
                self._observations.management_url = self._management.url

        logger.info(
            "MAS server started",
            extra={
                "listen": self._bound_addr,
                "agents_allowlisted": len(self._settings.agents),
            },
        )

    async def stop(self) -> None:
        """Stop gRPC server, Redis connection, and sessions."""
        async with self._lifecycle_lock:
            telemetry = get_telemetry()
            with (
                broker_scope(self._broker_id),
                telemetry.start_span("mas.server.stop", kind=SpanKind.INTERNAL),
            ):
                await self._finish_stop()

    async def _finish_stop(self) -> None:
        """Complete owned cleanup before propagating caller cancellation."""
        task = asyncio.create_task(self._stop_runtime())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _stop_runtime(self) -> None:
        """Stop runtime internals."""
        self._running = False
        if self._delivery:
            self._delivery.set_running(False)
        errors: list[Exception] = []

        if self._management is not None:
            try:
                await self._management.stop()
            except Exception as exc:
                errors.append(exc)
            else:
                self._management = None

        if self._grpc_server is not None:
            try:
                await self._grpc_server.stop(grace=2.0)
            except Exception as exc:
                errors.append(exc)
            else:
                self._grpc_server = None
                self._bound_addr = None

        sessions: list[Session] = []
        async with self._session_lock:
            if self._sessions:
                sessions = await self._sessions.snapshot_and_clear()

        for session in sessions:
            session.task.cancel()
        await asyncio.gather(
            *(session.task for session in sessions), return_exceptions=True
        )
        if sessions:
            with broker_scope(self._broker_id):
                get_telemetry().update_active_sessions(delta=-len(sessions))

        if self._audit is not None:
            try:
                await self._audit.close()
            except Exception as exc:
                errors.append(exc)

        if self._observations is not None:
            try:
                await self._observations.stop()
            except Exception as exc:
                errors.append(exc)
            else:
                self._observations = None

        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:
                errors.append(exc)
            else:
                self._redis = None

        self._authz = None
        self._audit = None
        self._rate_limit = None
        self._dlp = None
        self._circuit_breaker = None
        self._sessions = None
        self._registry = None
        self._state_store = None
        self._router = None
        self._delivery = None
        self._ingress = None

        logger.info("MAS server stopped")
        if errors:
            raise ExceptionGroup("Failed to close broker resources", errors)

    @property
    def broker_id(self) -> str:
        """Return this broker's stable configured or runtime-generated identity."""
        return self._broker_id

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

    @property
    def management_url(self) -> str:
        """Return the management URL when its listener is enabled and started."""
        if self._management is None:
            raise RuntimeError("Management dashboard is not enabled or started")
        return self._management.url

    async def connect_session(
        self,
        *,
        agent_id: str,
        instance_id: str,
    ) -> Session:
        """Create a session for a connecting agent instance."""
        async with self._session_lock:
            if not self._running:
                raise RuntimeError("Server not started")
            sessions = self._require_sessions()
            delivery = self._require_delivery()
            with broker_scope(self._broker_id):
                session = await sessions.connect(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    task_factory=delivery.start_stream_task,
                )
                get_telemetry().update_active_sessions(delta=1)
            return session

    async def disconnect_session(self, *, agent_id: str, instance_id: str) -> None:
        """Disconnect a session and update agent status."""
        async with self._session_lock:
            sessions = self._require_sessions()
            session, _remaining = await sessions.disconnect(
                agent_id=agent_id,
                instance_id=instance_id,
            )
            if session is not None:
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
                with broker_scope(self._broker_id):
                    get_telemetry().update_active_sessions(delta=-1)

    @_broker_operation
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

    @_broker_operation
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

    @_broker_operation
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

    @_broker_operation
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

    @_broker_operation
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

    @_broker_operation
    async def discover(
        self,
        *,
        agent_id: str,
        capabilities: list[str],
    ) -> list[AgentDiscoveryRecord]:
        """List discoverable agents for a sender and capability filter."""
        return await self._require_registry().discover(
            agent_id=agent_id,
            capabilities=capabilities,
        )

    @_broker_operation
    async def get_state(self, *, agent_id: str) -> dict[str, str]:
        """Return persisted state for an agent."""
        return await self._require_state_store().get_state(agent_id=agent_id)

    @_broker_operation
    async def get_state_snapshot(self, *, agent_id: str) -> StateSnapshot:
        """Return fields and their revision for optimistic concurrent updates."""
        return await self._require_state_store().snapshot(agent_id=agent_id)

    @_broker_operation
    async def update_state(
        self, *, agent_id: str, updates: dict[str, str], expected_revision: int
    ) -> int:
        """Update persisted agent state with provided fields."""
        return await self._require_state_store().update_state(
            agent_id=agent_id, updates=updates, expected_revision=expected_revision
        )

    @_broker_operation
    async def reset_state(self, *, agent_id: str, expected_revision: int) -> int:
        """Clear persisted agent state."""
        return await self._require_state_store().reset_state(
            agent_id=agent_id, expected_revision=expected_revision
        )

    def _build_policy_pipeline(self) -> PolicyPipeline:
        """Build policy pipeline from initialized modules."""
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

    @_broker_operation
    async def audit_authentication_denied(self, reason: str) -> None:
        """Record a stable reason without unverified identities or credentials."""
        if self._audit is not None:
            try:
                await self._audit.log_security_event(
                    "AUTHENTICATION_DENIED", {"reason": reason}
                )
            except Exception:
                logger.exception("Unable to persist authentication denial")
                raise

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
