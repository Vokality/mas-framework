"""Read-only broker management API and bundled dashboard."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.resources import files
from itertools import islice
from typing import TYPE_CHECKING, Literal

from aiohttp import web
from mas_core.observability import (
    ExportHealth,
    FleetSnapshot,
    ObservabilityStore,
    PerformancePoint,
    TraceSummary,
)
from mas_core.telemetry.runtime import TelemetrySnapshot, get_telemetry
from mas_gateway.audit import AuditModule
from mas_gateway.circuit_breaker import CircuitBreakerModule, CircuitStatus
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceResponse,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from .management_auth import (
    OidcAuthenticator,
    OidcSettings,
    OperatorAuthenticationError,
    OperatorIdentity,
)
from .observation import decode_otlp_spans

if TYPE_CHECKING:
    from mas_gateway.config import GatewaySettings

    from .sessions import SessionManager
    from .types import AgentDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelemetryIngestSettings:
    """A separate explicit write grant for the optional OTLP receiver."""

    auth_mode: Literal["local", "token", "oidc"] = "token"
    token: str | None = field(default=None, repr=False)
    oidc: OidcSettings | None = None
    max_payload_bytes: int = 1_048_576
    max_spans_per_request: int = 2_000

    def __post_init__(self) -> None:
        """Reject ambiguous credentials and unbounded collector requests."""
        if not 1 <= self.max_payload_bytes <= 16_777_216:
            raise ValueError("telemetry payload limit must be between 1 and 16777216")
        if not 1 <= self.max_spans_per_request <= 10_000:
            raise ValueError("telemetry span limit must be between 1 and 10000")
        if self.auth_mode == "oidc":
            if (
                self.oidc is None
                or self.token is not None
                or "mas:telemetry:write" not in self.oidc.required_scopes
            ):
                raise ValueError("telemetry OIDC requires mas:telemetry:write scope")
        elif self.auth_mode == "token":
            if self.oidc is not None or self.token is None:
                raise ValueError("telemetry token mode requires a separate token")
        elif (
            self.auth_mode != "local" or self.token is not None or self.oidc is not None
        ):
            raise ValueError("local telemetry mode does not accept credentials")
        if self.token is not None and re.fullmatch(r"[!-~]{1,512}", self.token) is None:
            raise ValueError(
                "telemetry token requires 1-512 printable ASCII characters"
            )


@dataclass(frozen=True, slots=True)
class ManagementTlsSettings:
    """Server certificate identity for direct management HTTPS."""

    cert_path: str
    key_path: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ManagementSettings:
    """Explicit local, development token, or OIDC operator access."""

    host: str = "127.0.0.1"
    port: int = 8080
    token: str | None = field(default=None, repr=False)
    auth_mode: Literal["local", "token", "oidc"] = "local"
    oidc: OidcSettings | None = None
    tls: ManagementTlsSettings | None = None
    telemetry_ingest: TelemetryIngestSettings | None = None

    def __post_init__(self) -> None:
        """Validate the listener before opening a socket."""
        if not 0 <= self.port <= 65535:
            raise ValueError("management port must be between 0 and 65535")
        loopback = (
            self.host == "localhost" or ipaddress.ip_address(self.host).is_loopback
        )
        if self.auth_mode == "oidc":
            if self.oidc is None or self.token is not None:
                raise ValueError("OIDC mode requires only OIDC provider configuration")
        elif self.auth_mode == "token":
            if self.token is None or self.oidc is not None:
                raise ValueError("token mode requires only a bearer token")
        elif (
            self.auth_mode != "local" or self.token is not None or self.oidc is not None
        ):
            raise ValueError("local mode does not accept authentication credentials")
        if not loopback and (self.auth_mode != "oidc" or self.tls is None):
            raise ValueError("remote management listeners require OIDC and HTTPS")
        if self.telemetry_ingest is not None:
            if not loopback and self.telemetry_ingest.auth_mode == "local":
                raise ValueError(
                    "local telemetry ingestion requires a loopback listener"
                )
            if (
                self.telemetry_ingest.token is not None
                and self.telemetry_ingest.token == self.token
            ):
                raise ValueError(
                    "telemetry ingestion requires credentials separate from readers"
                )
        if self.token is not None and (
            not self.token
            or not self.token.isascii()
            or not self.token.isprintable()
            or any(c.isspace() for c in self.token)
        ):
            raise ValueError(
                "management token must be non-empty printable ASCII without spaces"
            )


class HealthReport(BaseModel):
    """Readiness and dependency health, without backend exception details."""

    status: Literal["healthy", "degraded", "stopped"]
    redis_available: bool
    redis_latency_ms: float | None = None
    issues: list[str] = Field(default_factory=list)


class QueueSummary(BaseModel):
    """Durable work waiting for delivery or acknowledgement."""

    stream: str
    pending: int
    waiting: int | None


class SessionSummary(BaseModel):
    """Live instance and delivery-worker state."""

    instance_id: str
    inflight: int
    outbound: int
    worker_running: bool


class AgentSummary(BaseModel):
    """Shared agent availability and this broker's local connected instances."""

    agent_id: str
    capabilities: list[str]
    status: Literal["active", "inactive", "degraded", "unknown"]
    sessions: list[SessionSummary]


class ActivitySummary(BaseModel):
    """Policy decision metadata; message payloads and hashes are excluded."""

    message_id: str
    timestamp: float
    sender_id: str
    target_id: str
    message_type: str | None
    decision: str
    latency_ms: float
    correlation_id: str | None
    violations: list[str]


class ManagementSnapshot(BaseModel):
    """Typed management response, ready for presentation."""

    generated_at: float
    uptime_seconds: float
    health: HealthReport
    agents: list[AgentSummary]
    queues: list[QueueSummary] | None
    queues_complete: bool
    backlog: int | None
    dead_letters: int | None
    recent_activity: list[ActivitySummary] | None
    telemetry: TelemetrySnapshot
    features: dict[str, bool]
    circuits: dict[str, CircuitStatus] | None
    broker_id: str | None = None
    fleet: FleetSnapshot | None = None
    exporters: list[ExportHealth] = Field(default_factory=list)
    scope: str = (
        "Agent availability uses shared leases. Sessions and health are local to "
        "this broker; queues, audit and fleet observations use shared Redis. "
        "Telemetry counters are broker-scoped when a broker identity is configured, "
        "otherwise process-wide."
    )


class _StreamGroup(BaseModel):
    name: str
    pending: int = Field(ge=0)
    lag: int | None = Field(default=None, ge=0)


_STREAM_GROUPS = TypeAdapter(list[_StreamGroup])


@dataclass(frozen=True, slots=True)
class _DashboardAsset:
    """Immutable web resource read once before opening the listener."""

    body: bytes
    content_type: str


_DASHBOARD_FILES = (
    ("/", "index.html", "text/html"),
    ("/assets/dashboard.js", "assets/dashboard.js", "text/javascript"),
    ("/assets/dashboard.css", "assets/dashboard.css", "text/css"),
)
_DASHBOARD_PAGES = (
    "/",
    "/overview",
    "/fleet",
    "/performance",
    "/traces",
    "/alerts",
    "/agents",
    "/queues",
    "/activity",
    "/telemetry",
)


class _ReadLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(ge=1, le=500)


class ManagementService:
    """Serve health and a bounded, briefly cached management snapshot."""

    def __init__(
        self,
        *,
        settings: ManagementSettings,
        redis: Redis,
        sessions: SessionManager,
        agents: dict[str, AgentDefinition],
        gateway: GatewaySettings,
        audit: AuditModule,
        circuit_breaker: CircuitBreakerModule | None,
        is_running: Callable[[], bool],
        broker_id: str | None = None,
        observations: ObservabilityStore | None = None,
        observation_running: Callable[[], bool] | None = None,
    ) -> None:
        """Bind management reads to the broker's existing resources."""
        self._settings = settings
        self._redis = redis
        self._sessions = sessions
        self._agents = agents
        self._gateway = gateway
        self._audit = audit
        self._circuit_breaker = circuit_breaker
        self._is_running = is_running
        self._broker_id = broker_id
        self._observations = (
            observations if observations is not None else ObservabilityStore(redis)
        )
        self._observation_running = observation_running
        self._started_at = time.monotonic()
        self._runner: web.AppRunner | None = None
        self._url: str | None = None
        self._cache: ManagementSnapshot | None = None
        self._cache_at = 0.0
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._dashboard_assets: dict[str, _DashboardAsset] = {}
        self._authenticator = (
            OidcAuthenticator(settings.oidc) if settings.oidc else None
        )
        self._ingest_authenticator = (
            OidcAuthenticator(settings.telemetry_ingest.oidc)
            if settings.telemetry_ingest is not None
            and settings.telemetry_ingest.oidc is not None
            else None
        )

    @property
    def url(self) -> str:
        """Return the actual bound URL, including an ephemeral port."""
        if self._url is None:
            raise RuntimeError("Management server not started")
        return self._url

    async def start(self) -> None:
        """Start the read-only HTTP listener."""
        async with self._lifecycle_lock:
            if self._runner is not None:
                return
            await self._start_listener()

    async def _start_listener(self) -> None:
        """Create one listener while lifecycle ownership is held."""
        app = web.Application(
            client_max_size=self._settings.telemetry_ingest.max_payload_bytes
            if self._settings.telemetry_ingest is not None
            else 1024
        )
        app.router.add_get("/api/snapshot", self._snapshot_response)
        app.router.add_get("/api/history", self._history_response)
        app.router.add_get("/api/traces", self._traces_response)
        app.router.add_get("/api/traces/{trace_id}", self._trace_response)
        if self._settings.telemetry_ingest is not None:
            app.router.add_post("/v1/traces", self._ingest_response)
        app.router.add_get("/healthz", self._health_response)
        for path in _DASHBOARD_PAGES:
            app.router.add_get(path, self._dashboard)
        app.router.add_get("/traces/{trace_id:[a-f0-9]{32}}", self._dashboard)
        for path, _, _ in _DASHBOARD_FILES[1:]:
            app.router.add_get(path, self._dashboard_asset)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        self._runner = runner
        try:
            await runner.setup()
            tls = self._settings.tls

            def load_resources() -> tuple[
                dict[str, _DashboardAsset], ssl.SSLContext | None
            ]:
                bundle = files("mas_server").joinpath("dashboard_assets")
                assets = {
                    path: _DashboardAsset(
                        bundle.joinpath(filename).read_bytes(), content_type
                    )
                    for path, filename, content_type in _DASHBOARD_FILES
                }
                context: ssl.SSLContext | None = None
                if tls is not None:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.load_cert_chain(tls.cert_path, tls.key_path)
                return assets, context

            self._dashboard_assets, context = await asyncio.to_thread(load_resources)
            await web.TCPSite(
                runner, self._settings.host, self._settings.port, ssl_context=context
            ).start()
            port = runner.addresses[0][1]
            host = self._settings.host
            if ":" in host:
                host = f"[{host}]"
            scheme = "https" if context is not None else "http"
            self._url = f"{scheme}://{host}:{port}"
        except BaseException:
            await self._close_listener()
            raise

    async def stop(self) -> None:
        """Release HTTP resources even after a failed startup."""
        async with self._lifecycle_lock:
            await self._close_listener()

    async def _close_listener(self) -> None:
        """Wait for listener cleanup even when its caller is cancelled."""
        if self._runner is not None:
            task = asyncio.create_task(self._runner.cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            finally:
                self._runner = None
                self._url = None
        self._url = None
        if self._authenticator is not None:
            await self._authenticator.close()
        if self._ingest_authenticator is not None:
            await self._ingest_authenticator.close()

    async def health(self) -> HealthReport:
        """Check Redis with a bounded wait independent of queue scans."""
        if not self._is_running():
            return HealthReport(status="stopped", redis_available=False)
        start = time.monotonic()
        try:
            async with asyncio.timeout(2):
                await self._redis.ping()
        except (RedisError, OSError, TimeoutError):
            return HealthReport(
                status="degraded", redis_available=False, issues=["Redis unavailable"]
            )
        issues = [
            f"Delivery worker stopped for {session.agent_id}/{session.instance_id}"
            for session in await self._sessions.snapshot()
            if session.task.done()
        ]
        if self._observation_running is not None and not self._observation_running():
            issues.append("Observation worker stopped")
        return HealthReport(
            status="degraded" if issues else "healthy",
            redis_available=True,
            redis_latency_ms=round((time.monotonic() - start) * 1000, 3),
            issues=issues,
        )

    async def snapshot(self) -> ManagementSnapshot:
        """Gather operational state without exposing message data or secrets."""
        async with self._lock:
            if self._cache is not None and time.monotonic() - self._cache_at < 2:
                return self._cache.model_copy(deep=True)
            health = await self.health()
            sessions = await self._sessions.snapshot()
            instances_by_agent: dict[str, list[SessionSummary]] = {}
            for session in sessions:
                instances_by_agent.setdefault(session.agent_id, []).append(
                    SessionSummary(
                        instance_id=session.instance_id,
                        inflight=len(session.inflight),
                        outbound=session.outbound.qsize(),
                        worker_running=not session.task.done(),
                    )
                )
            availability: dict[str, bool] = {}
            if health.redis_available:
                try:
                    async with asyncio.timeout(2):
                        for agent_id in self._agents:
                            availability[agent_id] = await self._sessions.leases.active(
                                agent_id
                            )
                except (RedisError, OSError, TimeoutError, ValidationError):
                    health.status = "degraded"
                    health.issues.append("Shared agent availability unavailable")
            agents: list[AgentSummary] = []
            for agent_id, definition in sorted(self._agents.items()):
                instances = instances_by_agent.get(agent_id, [])
                status: Literal["active", "inactive", "degraded", "unknown"] = "unknown"
                active = availability.get(agent_id)
                if active is not None:
                    status = "active" if active else "inactive"
                if any(not session.worker_running for session in instances):
                    status = "degraded"
                if status == "degraded":
                    health.status = "degraded"
                agents.append(
                    AgentSummary(
                        agent_id=agent_id,
                        capabilities=list(definition.capabilities),
                        status=status,
                        sessions=instances,
                    )
                )
            queues: list[QueueSummary] | None = None
            queues_complete = False
            activity: list[ActivitySummary] | None = None
            circuits: dict[str, CircuitStatus] | None = (
                {} if self._circuit_breaker is None else None
            )
            dead_letters: int | None = None
            if health.redis_available:
                try:
                    async with asyncio.timeout(3):
                        stream_set: set[str] = set()
                        queues_complete = True
                        async for stream in self._redis.scan_iter(
                            match="agent.stream:*", count=100
                        ):
                            if stream in stream_set:
                                continue
                            if len(stream_set) >= 500:
                                health.issues.append(
                                    "Queue coverage limited to 500 streams"
                                )
                                queues_complete = False
                                break
                            stream_set.add(stream)
                        streams = sorted(stream_set)
                        queue_rows: list[QueueSummary] = []
                        async with self._redis.pipeline(transaction=False) as pipe:
                            for stream in streams:
                                pipe.xinfo_groups(stream)
                                pipe.xlen(stream)
                            results = await pipe.execute(raise_on_error=False)
                        for index, stream in enumerate(streams):
                            raw_groups = results[index * 2]
                            length = TypeAdapter(int).validate_python(
                                results[index * 2 + 1], strict=True
                            )
                            if isinstance(raw_groups, ResponseError):
                                if length == 0:
                                    continue
                                raise raw_groups
                            groups = _STREAM_GROUPS.validate_python(raw_groups)
                            group = next(
                                (g for g in groups if g.name == "agents"), None
                            )
                            queue_rows.append(
                                QueueSummary(
                                    stream=stream,
                                    pending=group.pending if group else 0,
                                    waiting=group.lag if group else length,
                                )
                            )
                        queues = queue_rows
                        dead_letters = await self._redis.xlen("dlq:messages")
                        entries = await self._audit.query_recent(count=40)
                        activity = [
                            ActivitySummary(
                                message_id=entry.message_id,
                                timestamp=entry.timestamp,
                                sender_id=entry.sender_id,
                                target_id=entry.target_id,
                                message_type=entry.message_type,
                                decision=entry.decision,
                                latency_ms=entry.latency_ms,
                                correlation_id=entry.correlation_id,
                                violations=entry.violations,
                            )
                            for entry in entries
                        ]
                        if self._circuit_breaker is not None:
                            circuits = await self._circuit_breaker.get_all_circuits(
                                limit=501
                            )
                            if len(circuits) > 500:
                                circuits = dict(islice(circuits.items(), 500))
                                health.issues.append(
                                    "Circuit coverage limited to 500 targets"
                                )
                except (RedisError, OSError, TimeoutError, ValidationError, ValueError):
                    logger.exception("Management snapshot read failed")
                    health.issues.append("Operational data unavailable or incomplete")
                if health.issues:
                    health.status = "degraded"
            backlog = None
            if (
                queues is not None
                and queues_complete
                and all(q.waiting is not None for q in queues)
            ):
                backlog = sum(q.pending + (q.waiting or 0) for q in queues)
            telemetry = get_telemetry()
            fleet: FleetSnapshot | None = None
            if health.redis_available:
                try:
                    async with asyncio.timeout(3):
                        fleet = await self._observations.fleet()
                except (RedisError, OSError, TimeoutError, ValidationError, ValueError):
                    health.status = "degraded"
                    health.issues.append("Fleet observations unavailable")
            snapshot = ManagementSnapshot(
                generated_at=time.time(),
                uptime_seconds=time.monotonic() - self._started_at,
                health=health,
                agents=agents,
                queues=queues,
                queues_complete=queues_complete,
                backlog=backlog,
                dead_letters=dead_letters,
                recent_activity=activity,
                telemetry=telemetry.snapshot(self._broker_id),
                broker_id=self._broker_id,
                fleet=fleet,
                exporters=telemetry.export_health(),
                features={
                    "dlp": self._gateway.features.dlp,
                    "rbac": self._gateway.features.rbac,
                    "circuit_breaker": self._gateway.features.circuit_breaker,
                    "tracing": telemetry.enabled and not telemetry.is_shutdown,
                },
                circuits=circuits,
            )
            self._cache = snapshot
            self._cache_at = time.monotonic()
            return snapshot.model_copy(deep=True)

    async def _authorize(self, request: web.Request, *, ingest: bool = False) -> None:
        credentials = self._settings.telemetry_ingest if ingest else self._settings
        if credentials is None:
            raise web.HTTPNotFound(headers=self._response_headers())
        if credentials.auth_mode == "local":
            if ingest:
                try:
                    local = (
                        request.remote is not None
                        and ipaddress.ip_address(request.remote).is_loopback
                    )
                except ValueError:
                    local = False
                if not local:
                    raise web.HTTPForbidden(
                        text="loopback_required", headers=self._response_headers()
                    )
            return
        authenticator = self._ingest_authenticator if ingest else self._authenticator
        identity: OperatorIdentity | None = None
        rejection: OperatorAuthenticationError | None = None
        try:
            if authenticator is not None:
                identity = await authenticator.authenticate(
                    request.headers.get("Authorization", "")
                )
            elif not hmac.compare_digest(
                request.headers.get("Authorization", "").encode("utf-8"),
                f"Bearer {credentials.token}".encode("ascii"),
            ):
                raise OperatorAuthenticationError("invalid_token")
            else:
                identity = OperatorIdentity(
                    "telemetry-token" if ingest else "development-token", "local"
                )
        except OperatorAuthenticationError as exc:
            rejection = exc
            identity = exc.identity
        try:
            async with asyncio.timeout(2):
                await self._audit.log_security_event(
                    event_type=(
                        "TELEMETRY_INGEST_DENIED"
                        if rejection
                        else "TELEMETRY_INGEST_ALLOWED"
                    )
                    if ingest
                    else (
                        "MANAGEMENT_ACCESS_DENIED"
                        if rejection
                        else "MANAGEMENT_ACCESS_ALLOWED"
                    ),
                    details={
                        "operator_id": identity.subject if identity else "anonymous",
                        "issuer": identity.issuer if identity else "unknown",
                        "route": request.path,
                        "reason": rejection.reason if rejection else "authorized",
                    },
                    instrumented=not ingest,
                )
        except (RedisError, OSError, TimeoutError):
            raise web.HTTPServiceUnavailable(
                text="audit_unavailable", headers=self._response_headers()
            ) from None
        if rejection is not None:
            if rejection.reason == "identity_provider_unavailable":
                raise web.HTTPServiceUnavailable(
                    text=rejection.reason, headers=self._response_headers()
                )
            if rejection.reason == "insufficient_access":
                raise web.HTTPForbidden(
                    text=rejection.reason, headers=self._response_headers()
                )
            raise web.HTTPUnauthorized(
                text=rejection.reason,
                headers={"WWW-Authenticate": "Bearer", **self._response_headers()},
            )

    @staticmethod
    def _response_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "style-src-attr 'unsafe-inline'; img-src 'self' data:; "
                "font-src 'self'; connect-src 'self'; base-uri 'none'; "
                "object-src 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        }

    async def _dashboard(self, request: web.Request) -> web.Response:
        """Serve a static shell; operational reads require authorization."""
        if request.match_info.get("trace_id") == "0" * 32:
            raise web.HTTPNotFound(headers=self._response_headers())
        asset = self._dashboard_assets["/"]
        return web.Response(
            body=asset.body,
            content_type=asset.content_type,
            charset="utf-8",
            headers=self._response_headers(),
        )

    async def _dashboard_asset(self, request: web.Request) -> web.Response:
        """Serve only startup-loaded assets, without filesystem path resolution."""
        asset = self._dashboard_assets[request.path]
        return web.Response(
            body=asset.body,
            content_type=asset.content_type,
            charset="utf-8",
            headers=self._response_headers(),
        )

    async def _snapshot_response(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        snapshot = await self.snapshot()
        return web.Response(
            text=snapshot.model_dump_json(),
            content_type="application/json",
            headers=self._response_headers(),
        )

    async def _health_response(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        health = await self.health()
        return web.Response(
            text=health.model_dump_json(),
            content_type="application/json",
            status=200 if health.status == "healthy" else 503,
            headers=self._response_headers(),
        )

    @staticmethod
    def _read_limit(request: web.Request, *, default: int) -> int:
        """Reject unknown, repeated and out-of-range reader parameters."""
        if len(request.query) != len(set(request.query)):
            raise web.HTTPBadRequest(
                text="invalid_query", headers=ManagementService._response_headers()
            )
        try:
            return _ReadLimit.model_validate({"limit": default, **request.query}).limit
        except ValidationError:
            raise web.HTTPBadRequest(
                text="invalid_query", headers=ManagementService._response_headers()
            ) from None

    async def _read_observations[ResultT](
        self, operation: Callable[[], Awaitable[ResultT]]
    ) -> ResultT:
        """Keep retained-data storage failures sanitized and bounded."""
        try:
            async with asyncio.timeout(3):
                return await operation()
        except (RedisError, OSError, TimeoutError, ValidationError, ValueError):
            raise web.HTTPServiceUnavailable(
                text="observations_unavailable", headers=self._response_headers()
            ) from None

    async def _history_response(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        limit = self._read_limit(request, default=120)
        points = await self._read_observations(
            lambda: self._observations.history(limit)
        )
        return web.Response(
            body=TypeAdapter(list[PerformancePoint]).dump_json(points),
            content_type="application/json",
            headers=self._response_headers(),
        )

    async def _traces_response(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        limit = self._read_limit(request, default=50)
        traces = await self._read_observations(lambda: self._observations.traces(limit))
        return web.Response(
            body=TypeAdapter(list[TraceSummary]).dump_json(traces),
            content_type="application/json",
            headers=self._response_headers(),
        )

    async def _trace_response(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        trace_id = request.match_info["trace_id"]
        if (
            request.query
            or re.fullmatch(r"[0-9a-f]{32}", trace_id) is None
            or not any(value != "0" for value in trace_id)
        ):
            raise web.HTTPBadRequest(
                text="invalid_trace_id", headers=self._response_headers()
            )
        trace = await self._read_observations(
            lambda: self._observations.trace(trace_id)
        )
        if trace is None:
            raise web.HTTPNotFound(
                text="trace_not_found", headers=self._response_headers()
            )
        return web.Response(
            text=trace.model_dump_json(),
            content_type="application/json",
            headers=self._response_headers(),
        )

    async def _ingest_response(self, request: web.Request) -> web.Response:
        await self._authorize(request, ingest=True)
        settings = self._settings.telemetry_ingest
        if settings is None:
            raise web.HTTPNotFound(headers=self._response_headers())
        if request.content_type != "application/x-protobuf":
            raise web.HTTPUnsupportedMediaType(
                text="protobuf_required", headers=self._response_headers()
            )
        payload = await request.read()
        try:
            spans = await asyncio.to_thread(
                decode_otlp_spans, payload, limit=settings.max_spans_per_request
            )
        except (ValueError, ValidationError) as exc:
            if str(exc) == "too_many_spans":
                raise web.HTTPRequestEntityTooLarge(
                    max_size=settings.max_spans_per_request,
                    actual_size=settings.max_spans_per_request + 1,
                    headers=self._response_headers(),
                ) from None
            raise web.HTTPBadRequest(
                text="invalid_telemetry", headers=self._response_headers()
            ) from None
        await self._read_observations(lambda: self._observations.ingest_spans(spans))
        return web.Response(
            body=ExportTraceServiceResponse().SerializeToString(),
            content_type="application/x-protobuf",
            headers=self._response_headers(),
        )
