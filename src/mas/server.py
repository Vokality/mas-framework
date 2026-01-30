"""MAS gRPC server and Redis-backed routing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from math import ceil
from typing import Any, AsyncIterator

import grpc
import grpc.aio as grpc_aio

from ._proto.v1 import mas_pb2, mas_pb2_grpc
from .gateway.audit import AuditFileSink, AuditModule
from .gateway.authorization import AuthorizationModule
from .gateway.circuit_breaker import CircuitBreakerConfig, CircuitBreakerModule
from .gateway.config import GatewaySettings, RedisSettings
from .gateway.dlp import ActionPolicy, DLPModule
from .gateway.rate_limit import RateLimitModule
from .protocol import EnvelopeMessage, MessageMeta
from .redis_client import create_redis_client
from .redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


_SPIFFE_RE = re.compile(r"^spiffe://mas/agent/(?P<agent_id>[a-zA-Z0-9_-]{1,64})$")
_INSTANCE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Agent allowlist entry and metadata."""

    agent_id: str
    capabilities: list[str]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TlsConfig:
    """Server-side TLS credential paths."""

    server_cert_path: str
    server_key_path: str
    client_ca_path: str


@dataclass(frozen=True, slots=True)
class MASServerSettings:
    """Configuration for MAS server runtime."""

    redis_url: str
    listen_addr: str
    tls: TlsConfig
    agents: dict[str, AgentDefinition]

    reclaim_idle_ms: int = 30_000
    reclaim_batch_size: int = 50
    max_in_flight: int = 200


@dataclass(slots=True)
class _InflightDelivery:
    """Delivery state tracked while awaiting ACK/NACK."""

    stream_name: str
    group: str
    entry_id: str
    envelope_json: str
    received_at: float


@dataclass(slots=True)
class _Session:
    """Active agent session for a single instance."""

    agent_id: str
    instance_id: str
    outbound: asyncio.Queue[mas_pb2.ServerEvent]
    inflight: dict[str, _InflightDelivery]
    task: asyncio.Task[None]


class MASServer:
    """MAS server (gRPC + mTLS) that owns all Redis responsibilities."""

    def __init__(
        self,
        *,
        settings: MASServerSettings,
        gateway: GatewaySettings | None = None,
    ):
        """Initialize server state and gateway modules."""
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

        self._sessions: dict[tuple[str, str], _Session] = {}
        self._sessions_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start Redis connection, modules, and gRPC server."""
        redis_conn = create_redis_client(
            url=self._gateway_settings.redis.url,
            decode_responses=self._gateway_settings.redis.decode_responses,
            socket_timeout=self._gateway_settings.redis.socket_timeout,
        )
        self._redis = redis_conn

        # Core modules
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

        # Bootstrap agent records (allowlist is authoritative)
        await self._bootstrap_registry()

        # Start gRPC server
        server = grpc_aio.server()
        mas_pb2_grpc.add_MasServiceServicer_to_server(_MasGrpcServicer(self), server)

        creds = _load_server_credentials(self._settings.tls)
        port = server.add_secure_port(self._settings.listen_addr, creds)
        if self._settings.listen_addr.endswith(":0"):
            host = self._settings.listen_addr.rsplit(":", 1)[0]
            self._bound_addr = f"{host}:{port}"
        else:
            self._bound_addr = self._settings.listen_addr
        await server.start()
        self._grpc_server = server

        self._running = True
        logger.info(
            "MAS server started",
            extra={
                "listen": self._bound_addr,
                "agents_allowlisted": len(self._settings.agents),
            },
        )

    async def stop(self) -> None:
        """Stop gRPC server, Redis connection, and sessions."""
        self._running = False

        # Stop sessions
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for sess in sessions:
            sess.task.cancel()

        await asyncio.gather(*(s.task for s in sessions), return_exceptions=True)

        if self._grpc_server is not None:
            await self._grpc_server.stop(grace=2.0)
            self._grpc_server = None
            self._bound_addr = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        logger.info("MAS server stopped")

    @property
    def authz(self) -> AuthorizationModule:
        """Return the authorization module after startup."""
        if not self._authz:
            raise RuntimeError("Server not started")
        return self._authz

    @property
    def bound_addr(self) -> str:
        """Return the bound listen address after startup."""
        if not self._bound_addr:
            raise RuntimeError("Server not started")
        return self._bound_addr

    # --- gRPC entrypoints (called by servicer) ---

    async def connect_session(
        self,
        *,
        agent_id: str,
        instance_id: str,
    ) -> _Session:
        """Create a session for a connecting agent instance."""
        if agent_id not in self._settings.agents:
            raise _UnauthenticatedError("agent_not_allowlisted")
        if not _INSTANCE_RE.match(instance_id):
            raise _InvalidArgumentError("invalid_instance_id")

        assert self._redis is not None

        key = (agent_id, instance_id)
        async with self._sessions_lock:
            if key in self._sessions:
                raise _FailedPreconditionError("instance_already_connected")

            outbound: asyncio.Queue[mas_pb2.ServerEvent] = asyncio.Queue(maxsize=500)
            inflight: dict[str, _InflightDelivery] = {}
            task = asyncio.create_task(
                self._stream_loop(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    outbound=outbound,
                    inflight=inflight,
                )
            )
            session = _Session(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
                task=task,
            )
            self._sessions[key] = session

        # Mark ACTIVE on first connection
        await self._set_agent_status(agent_id, "ACTIVE")
        return session

    async def disconnect_session(self, *, agent_id: str, instance_id: str) -> None:
        """Disconnect a session and update agent status."""
        key = (agent_id, instance_id)
        async with self._sessions_lock:
            sess = self._sessions.pop(key, None)
            remaining = any(aid == agent_id for (aid, _iid) in self._sessions.keys())

        if sess is not None:
            sess.task.cancel()
            await asyncio.gather(sess.task, return_exceptions=True)

        if not remaining:
            await self._set_agent_status(agent_id, "INACTIVE")

    async def handle_ack(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
    ) -> None:
        """Handle delivery ACK and update inflight state."""
        inflight = await self._pop_inflight_delivery(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
        )
        if not inflight:
            return

        assert self._redis is not None
        await self._ack_inflight(inflight)

        if self._circuit_breaker:
            await self._circuit_breaker.record_success(agent_id)

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
        inflight = await self._pop_inflight_delivery(
            agent_id=agent_id,
            instance_id=instance_id,
            delivery_id=delivery_id,
        )
        if not inflight:
            return

        assert self._redis is not None
        if retryable:
            # Requeue by ACKing and re-adding to the same stream.
            try:
                await self._ack_inflight(inflight)
                await self._redis.xadd(
                    inflight.stream_name, {"envelope": inflight.envelope_json}
                )
            except Exception:
                pass
        else:
            await self._write_dlq(envelope_json=inflight.envelope_json, reason=reason)
            await self._ack_inflight(inflight)

        if self._circuit_breaker:
            await self._circuit_breaker.record_failure(agent_id, reason=reason)

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
        self._ensure_connected(sender_id, sender_instance_id)
        payload = _parse_payload_json(data_json)
        msg = self._build_message(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(sender_instance_id=sender_instance_id),
        )
        await self._ingest_and_route(msg)
        return msg.message_id

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
        self._ensure_connected(sender_id, sender_instance_id)
        assert self._redis is not None
        payload = _parse_payload_json(data_json)
        correlation_id = uuid.uuid4().hex

        ttl_seconds = max(1, int(ceil(timeout_ms / 1000.0))) if timeout_ms > 0 else 60
        expires_at = time.time() + float(ttl_seconds)
        await self._redis.setex(
            f"mas.pending_request:{correlation_id}",
            ttl_seconds,
            json.dumps(
                {
                    "agent_id": sender_id,
                    "instance_id": sender_instance_id,
                    "expires_at": expires_at,
                }
            ),
        )

        msg = self._build_message(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                expects_reply=True,
            ),
        )
        await self._ingest_and_route(msg)
        return msg.message_id, correlation_id

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
        self._ensure_connected(sender_id, sender_instance_id)
        assert self._redis is not None
        payload = _parse_payload_json(data_json)

        if not correlation_id:
            raise _InvalidArgumentError("missing_correlation_id")

        pending_key = f"mas.pending_request:{correlation_id}"
        origin_raw = await self._redis.get(pending_key)
        if not origin_raw:
            raise _InvalidArgumentError("unknown_correlation_id")

        if isinstance(origin_raw, bytes):
            try:
                origin_text = origin_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _InvalidArgumentError("unknown_correlation_id") from exc
        else:
            origin_text = str(origin_raw)

        try:
            origin_obj = json.loads(origin_text)
        except json.JSONDecodeError as exc:
            raise _InvalidArgumentError("unknown_correlation_id") from exc

        if not isinstance(origin_obj, dict):
            raise _InvalidArgumentError("unknown_correlation_id")

        origin_agent_id = str(origin_obj.get("agent_id", ""))
        origin_instance_id = str(origin_obj.get("instance_id", ""))
        expires_at_raw = origin_obj.get("expires_at")
        if expires_at_raw is None:
            raise _InvalidArgumentError("unknown_correlation_id")
        if isinstance(expires_at_raw, (int, float)):
            expires_at = float(expires_at_raw)
        elif isinstance(expires_at_raw, str):
            try:
                expires_at = float(expires_at_raw)
            except ValueError as exc:
                raise _InvalidArgumentError("unknown_correlation_id") from exc
        else:
            raise _InvalidArgumentError("unknown_correlation_id")

        if not origin_agent_id or not origin_instance_id:
            raise _InvalidArgumentError("unknown_correlation_id")

        if time.time() > expires_at:
            try:
                await self._redis.delete(pending_key)
            except Exception:
                pass
            raise _FailedPreconditionError("correlation_id_expired")

        msg = self._build_message(
            sender_id=sender_id,
            target_id=origin_agent_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                sender_instance_id=sender_instance_id,
                correlation_id=correlation_id,
                is_reply=True,
                expects_reply=False,
                reply_to_instance_id=origin_instance_id,
            ),
        )
        await self._ingest_and_route(msg)

        # Prevent duplicate replies once successfully routed.
        try:
            await self._redis.delete(pending_key)
        except Exception:
            pass
        return msg.message_id

    async def discover(
        self,
        *,
        agent_id: str,
        capabilities: list[str],
    ) -> list[dict[str, Any]]:
        """List discoverable agents for a sender and capability filter."""
        # Secure-by-default: only return targets this agent can send to.
        assert self._redis is not None
        assert self._authz is not None

        allowed_key = f"agent:{agent_id}:allowed_targets"
        blocked_key = f"agent:{agent_id}:blocked_targets"
        allowed = await self._redis.smembers(allowed_key)
        blocked = await self._redis.smembers(blocked_key)

        candidates: list[str]
        if "*" in allowed:
            candidates = list(self._settings.agents.keys())
        else:
            candidates = [t for t in allowed if t]

        candidates = [t for t in candidates if t not in blocked]

        results: list[dict[str, Any]] = []
        for target in candidates:
            if target not in self._settings.agents:
                continue
            status = await self._redis.hget(f"agent:{target}", "status")
            if status != "ACTIVE":
                continue

            definition = self._settings.agents[target]
            if capabilities and not any(
                c in definition.capabilities for c in capabilities
            ):
                continue

            results.append(
                {
                    "id": definition.agent_id,
                    "capabilities": list(definition.capabilities),
                    "metadata": dict(definition.metadata),
                    "status": status,
                }
            )

        return results

    async def get_state(self, *, agent_id: str) -> dict[str, str]:
        """Return persisted state for an agent."""
        assert self._redis is not None
        return await self._redis.hgetall(f"agent.state:{agent_id}")

    async def update_state(self, *, agent_id: str, updates: dict[str, str]) -> None:
        """Update persisted agent state with provided fields."""
        assert self._redis is not None
        if updates:
            await self._redis.hset(f"agent.state:{agent_id}", mapping=updates)

    async def reset_state(self, *, agent_id: str) -> None:
        """Clear persisted agent state."""
        assert self._redis is not None
        await self._redis.delete(f"agent.state:{agent_id}")

    def _ensure_connected(self, agent_id: str, instance_id: str) -> None:
        """Validate a connected session for sender."""
        if not _INSTANCE_RE.match(instance_id):
            raise _InvalidArgumentError("invalid_instance_id")
        key = (agent_id, instance_id)
        if key not in self._sessions:
            raise _FailedPreconditionError("session_not_connected")

    @staticmethod
    def _build_message(
        *,
        sender_id: str,
        target_id: str,
        message_type: str,
        data: dict[str, Any],
        meta: MessageMeta,
    ) -> EnvelopeMessage:
        """Build a message envelope from parsed payload and meta."""
        return EnvelopeMessage(
            sender_id=sender_id,
            target_id=target_id,
            message_type=message_type,
            data=data,
            meta=meta,
        )

    async def _pop_inflight_delivery(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
    ) -> _InflightDelivery | None:
        """Remove and return an inflight delivery for a session."""
        if not delivery_id:
            return None

        key = (agent_id, instance_id)
        async with self._sessions_lock:
            sess = self._sessions.get(key)

        if not sess:
            return None

        return sess.inflight.pop(delivery_id, None)

    # --- internals ---

    async def _bootstrap_registry(self) -> None:
        """Populate Redis agent records from allowlist."""
        assert self._redis is not None
        now = str(time.time())
        pipe = self._redis.pipeline()
        for agent_id, definition in self._settings.agents.items():
            agent_key = f"agent:{agent_id}"
            pipe.hset(
                agent_key,
                mapping={
                    "id": agent_id,
                    "capabilities": json.dumps(definition.capabilities),
                    "metadata": json.dumps(definition.metadata),
                    "status": "INACTIVE",
                    "registered_at": now,
                },
            )
        await pipe.execute()

    async def _set_agent_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status field in Redis."""
        assert self._redis is not None
        await self._redis.hset(
            f"agent:{agent_id}",
            mapping={"status": status, "registered_at": str(time.time())},
        )

    async def _write_dlq(self, *, envelope_json: str, reason: str) -> None:
        """Write a message to the DLQ stream for auditing."""
        if not self._redis:
            return
        if not self._audit:
            return

        try:
            msg = EnvelopeMessage.model_validate_json(envelope_json)
        except Exception:
            return

        envelope_hash = hashlib.sha256(envelope_json.encode()).hexdigest()
        fields: dict[str, str] = {
            "message_id": msg.message_id,
            "sender_id": msg.sender_id,
            "sender_instance_id": msg.meta.sender_instance_id or "",
            "target_id": msg.target_id,
            "message_type": msg.message_type,
            "decision": "DLQ",
            "reason": reason,
            "envelope_hash": envelope_hash,
            "timestamp": str(time.time()),
        }
        try:
            await self._redis.xadd("dlq:messages", fields)
        except Exception:
            pass

    async def _ack_inflight(self, inflight: _InflightDelivery) -> None:
        """Best-effort ACK for a stream entry."""
        assert self._redis is not None
        try:
            await self._redis.xack(
                inflight.stream_name, inflight.group, inflight.entry_id
            )
        except Exception:
            # Best-effort; message will be reclaimed if needed.
            pass

    async def _ingest_and_route(self, message: EnvelopeMessage) -> None:
        """Run policy checks, route, and audit the message."""
        assert self._authz is not None
        assert self._rate_limit is not None
        assert self._audit is not None
        assert self._redis is not None

        audit = self._audit

        start = time.time()

        async def log_and_raise(
            decision: str, violations: list[str], exc: _RpcError
        ) -> None:
            """Log an audit entry and raise a gRPC error."""
            latency_ms = (time.time() - start) * 1000
            await audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                decision,
                latency_ms,
                message.data,
                violations=violations,
                message_type=message.message_type,
                correlation_id=message.meta.correlation_id,
                sender_instance_id=message.meta.sender_instance_id,
            )
            raise exc

        # Authorization
        authorized = await self._authz.authorize(
            message.sender_id, message.target_id, action="send"
        )
        if not authorized:
            await log_and_raise(
                "AUTHZ_DENIED",
                ["authorization_denied"],
                _PermissionDeniedError("not_authorized"),
            )

        # Rate limit
        rate = await self._rate_limit.check_rate_limit(
            message.sender_id, message.message_id
        )
        if not rate.allowed:
            await log_and_raise(
                "RATE_LIMITED",
                ["rate_limit_exceeded"],
                _ResourceExhaustedError("rate_limited"),
            )

        # Circuit breaker (block if open)
        if self._circuit_breaker:
            status = await self._circuit_breaker.check_circuit(message.target_id)
            if not status.allowed:
                await log_and_raise(
                    "CIRCUIT_OPEN",
                    ["circuit_open"],
                    _FailedPreconditionError("circuit_open"),
                )

        # DLP
        decision = "ALLOWED"
        violations: list[str] = []
        if self._dlp:
            scan = await self._dlp.scan(message.data)
            if not scan.clean:
                violations.extend([v.violation_type for v in scan.violations])

                if scan.action == ActionPolicy.BLOCK:
                    await log_and_raise(
                        "DLP_BLOCKED",
                        violations,
                        _PermissionDeniedError("dlp_blocked"),
                    )

                if scan.action == ActionPolicy.ALERT:
                    decision = "ALERT"
                elif scan.action == ActionPolicy.REDACT:
                    decision = "DLP_REDACTED"
                elif scan.action == ActionPolicy.ENCRYPT:
                    decision = "DLP_ENCRYPTED"

                if scan.action == ActionPolicy.REDACT and scan.redacted_payload:
                    message.data = scan.redacted_payload

        # Route
        await self._route_message(message)

        latency_ms = (time.time() - start) * 1000
        await self._audit.log_message(
            message.message_id,
            message.sender_id,
            message.target_id,
            decision,
            latency_ms,
            message.data,
            violations=violations,
            message_type=message.message_type,
            correlation_id=message.meta.correlation_id,
            sender_instance_id=message.meta.sender_instance_id,
        )

    async def _route_message(self, message: EnvelopeMessage) -> None:
        """Write message payload to the appropriate Redis stream."""
        assert self._redis is not None
        envelope_json = message.model_dump_json()
        fields = {"envelope": envelope_json}

        if message.meta.is_reply:
            if not message.meta.reply_to_instance_id:
                raise _InvalidArgumentError("missing_reply_to_instance_id")
            stream = (
                f"agent.stream:{message.target_id}:{message.meta.reply_to_instance_id}"
            )
        else:
            stream = f"agent.stream:{message.target_id}"

        await self._redis.xadd(stream, fields)

    async def _stream_loop(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, _InflightDelivery],
    ) -> None:
        """Consume messages from Redis streams and deliver over gRPC."""
        assert self._redis is not None

        shared_stream = f"agent.stream:{agent_id}"
        instance_stream = f"agent.stream:{agent_id}:{instance_id}"
        group = "agents"
        consumer = f"{agent_id}-{instance_id}"

        for stream in (shared_stream, instance_stream):
            try:
                await self._redis.xgroup_create(stream, group, id="$", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

        claim_start_ids: dict[str, str] = {shared_stream: "0-0", instance_stream: "0-0"}
        last_reclaim = 0.0
        reclaim_interval = max(1.0, self._settings.reclaim_idle_ms / 1000.0)

        try:
            while self._running:
                # Backpressure
                if len(inflight) >= self._settings.max_in_flight:
                    await asyncio.sleep(0.05)
                    continue

                now = time.time()
                if now - last_reclaim >= reclaim_interval:
                    for stream_name in (shared_stream, instance_stream):
                        claim_start_ids[stream_name] = await self._reclaim_pending(
                            stream_name,
                            group,
                            consumer,
                            claim_start_ids[stream_name],
                            agent_id=agent_id,
                            instance_id=instance_id,
                            outbound=outbound,
                            inflight=inflight,
                        )
                    last_reclaim = now

                items = await self._redis.xreadgroup(
                    group,
                    consumer,
                    streams={shared_stream: ">", instance_stream: ">"},
                    count=50,
                    block=1000,
                )
                if not items:
                    continue

                for stream_name, messages in items:
                    for entry_id, fields in messages:
                        envelope_json = fields.get("envelope", "")
                        if not envelope_json:
                            try:
                                await self._redis.xack(stream_name, group, entry_id)
                            except Exception:
                                pass
                            continue

                        await self._deliver_entry(
                            agent_id=agent_id,
                            instance_id=instance_id,
                            outbound=outbound,
                            inflight=inflight,
                            stream_name=stream_name,
                            group=group,
                            entry_id=entry_id,
                            envelope_json=envelope_json,
                        )
        except asyncio.CancelledError:
            pass

    async def _reclaim_pending(
        self,
        stream_name: str,
        group: str,
        consumer: str,
        start_id: str,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, _InflightDelivery],
    ) -> str:
        """Reclaim idle pending messages for delivery."""
        assert self._redis is not None

        try:
            next_start_id, messages, _deleted_ids = await self._redis.xautoclaim(
                stream_name,
                group,
                consumer,
                self._settings.reclaim_idle_ms,
                start_id,
                count=self._settings.reclaim_batch_size,
            )
        except Exception:
            return start_id

        for entry_id, fields in messages:
            envelope_json = fields.get("envelope", "")
            if not envelope_json:
                try:
                    await self._redis.xack(stream_name, group, entry_id)
                except Exception:
                    pass
                continue

            # Deliver reclaimed pending messages as normal.
            await self._deliver_entry(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
                stream_name=stream_name,
                group=group,
                entry_id=entry_id,
                envelope_json=envelope_json,
            )

        return next_start_id

    async def _deliver_entry(
        self,
        *,
        agent_id: str,
        instance_id: str,
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, _InflightDelivery],
        stream_name: str,
        group: str,
        entry_id: str,
        envelope_json: str,
    ) -> None:
        """Send a single stream entry to the client."""
        delivery_id = uuid.uuid4().hex
        event = mas_pb2.ServerEvent(
            delivery=mas_pb2.Delivery(
                delivery_id=delivery_id,
                envelope_json=envelope_json,
            )
        )
        dropped = self._drop_oldest_outbound(outbound, inflight)
        if dropped:
            logger.warning(
                "Outbound queue full; dropped oldest deliveries",
                extra={
                    "agent_id": agent_id,
                    "instance_id": instance_id,
                    "dropped": dropped,
                },
            )
        try:
            outbound.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Outbound queue full; dropping new delivery",
                extra={
                    "agent_id": agent_id,
                    "instance_id": instance_id,
                    "delivery_id": delivery_id,
                },
            )
            return

        inflight[delivery_id] = _InflightDelivery(
            stream_name=stream_name,
            group=group,
            entry_id=entry_id,
            envelope_json=envelope_json,
            received_at=time.time(),
        )

    @staticmethod
    def _drop_oldest_outbound(
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, _InflightDelivery],
    ) -> int:
        """Drop oldest outbound events to make room."""
        if outbound.maxsize <= 0 or not outbound.full():
            return 0

        dropped = 0
        while outbound.full():
            try:
                event = outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            if event.HasField("delivery"):
                delivery_id = event.delivery.delivery_id
                inflight.pop(delivery_id, None)
        return dropped


class _MasGrpcServicer(mas_pb2_grpc.MasServiceServicer):
    """gRPC servicer wiring to MASServer operations."""

    def __init__(self, server: MASServer):
        """Initialize servicer with a MASServer."""
        self._server = server

    async def _agent_id_or_abort(self, context: grpc_aio.ServicerContext) -> str | None:
        """Resolve agent id or abort the RPC."""
        try:
            return _spiffe_agent_id(context)
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return None

    async def Transport(
        self,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        context: grpc_aio.ServicerContext,
    ) -> AsyncIterator[mas_pb2.ServerEvent]:
        """Handle bidirectional transport stream."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return

        # First message must be Hello with instance_id
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "missing_hello")
            return

        if not first.HasField("hello"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "expected_hello")
            return

        instance_id = first.hello.instance_id

        try:
            session = await self._server.connect_session(
                agent_id=agent_id,
                instance_id=instance_id,
            )
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return

        # Kick off inbound processing
        inbound_task = asyncio.create_task(
            self._consume_client_events(
                request_iterator=request_iterator,
                agent_id=agent_id,
                instance_id=instance_id,
            )
        )

        # Welcome
        yield mas_pb2.ServerEvent(
            welcome=mas_pb2.Welcome(agent_id=agent_id, instance_id=instance_id)
        )

        try:
            while True:
                event = await session.outbound.get()
                yield event
        except asyncio.CancelledError:
            pass
        finally:
            inbound_task.cancel()
            await asyncio.gather(inbound_task, return_exceptions=True)
            await self._server.disconnect_session(
                agent_id=agent_id, instance_id=instance_id
            )

    async def Send(
        self, request: mas_pb2.SendRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.SendResponse:
        """Handle one-way send requests."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.SendResponse()
        sender_instance_id = request.instance_id
        try:
            message_id = await self._server.send_message(
                sender_id=sender_id,
                sender_instance_id=sender_instance_id,
                target_id=request.target_id,
                message_type=request.message_type,
                data_json=request.data_json,
            )
            return mas_pb2.SendResponse(message_id=message_id)
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.SendResponse()

    async def Request(
        self, request: mas_pb2.RequestRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.RequestResponse:
        """Handle request-response messages."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.RequestResponse()
        sender_instance_id = request.instance_id
        try:
            message_id, correlation_id = await self._server.request_message(
                sender_id=sender_id,
                sender_instance_id=sender_instance_id,
                target_id=request.target_id,
                message_type=request.message_type,
                data_json=request.data_json,
                timeout_ms=request.timeout_ms,
            )
            return mas_pb2.RequestResponse(
                message_id=message_id, correlation_id=correlation_id
            )
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.RequestResponse()

    async def Reply(
        self, request: mas_pb2.ReplyRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.ReplyResponse:
        """Handle replies to pending requests."""
        sender_id = await self._agent_id_or_abort(context)
        if sender_id is None:
            return mas_pb2.ReplyResponse()
        sender_instance_id = request.instance_id
        try:
            message_id = await self._server.reply_message(
                sender_id=sender_id,
                sender_instance_id=sender_instance_id,
                correlation_id=request.correlation_id,
                message_type=request.message_type,
                data_json=request.data_json,
            )
            return mas_pb2.ReplyResponse(message_id=message_id)
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.ReplyResponse()

    async def Discover(
        self, request: mas_pb2.DiscoverRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.DiscoverResponse:
        """Handle discovery requests."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.DiscoverResponse()
        try:
            records = await self._server.discover(
                agent_id=agent_id, capabilities=list(request.capabilities)
            )
        except _RpcError as exc:
            await context.abort(exc.status, exc.message)
            return mas_pb2.DiscoverResponse()

        agents: list[mas_pb2.AgentRecord] = []
        for rec in records:
            agents.append(
                mas_pb2.AgentRecord(
                    agent_id=rec["id"],
                    capabilities=list(rec["capabilities"]),
                    metadata_json=json.dumps(rec["metadata"]),
                    status=str(rec["status"]),
                )
            )
        return mas_pb2.DiscoverResponse(agents=agents)

    async def GetState(
        self, request: mas_pb2.GetStateRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.GetStateResponse:
        """Return persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.GetStateResponse()
        state = await self._server.get_state(agent_id=agent_id)
        return mas_pb2.GetStateResponse(state=state)

    async def UpdateState(
        self, request: mas_pb2.UpdateStateRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.UpdateStateResponse:
        """Update persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.UpdateStateResponse()
        await self._server.update_state(
            agent_id=agent_id, updates=dict(request.updates)
        )
        return mas_pb2.UpdateStateResponse()

    async def ResetState(
        self, request: mas_pb2.ResetStateRequest, context: grpc_aio.ServicerContext
    ) -> mas_pb2.ResetStateResponse:
        """Reset persisted state for the caller."""
        agent_id = await self._agent_id_or_abort(context)
        if agent_id is None:
            return mas_pb2.ResetStateResponse()
        await self._server.reset_state(agent_id=agent_id)
        return mas_pb2.ResetStateResponse()

    async def _consume_client_events(
        self,
        *,
        request_iterator: AsyncIterator[mas_pb2.ClientEvent],
        agent_id: str,
        instance_id: str,
    ) -> None:
        """Consume inbound ACK/NACK events."""
        async for event in request_iterator:
            if event.HasField("ack"):
                await self._server.handle_ack(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    delivery_id=event.ack.delivery_id,
                )
            elif event.HasField("nack"):
                await self._server.handle_nack(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    delivery_id=event.nack.delivery_id,
                    reason=event.nack.reason,
                    retryable=event.nack.retryable,
                )


def _parse_payload_json(data_json: str) -> dict[str, Any]:
    """Parse payload JSON into a dictionary."""
    try:
        obj = json.loads(data_json) if data_json else {}
    except json.JSONDecodeError as exc:
        raise _InvalidArgumentError("invalid_json") from exc
    if not isinstance(obj, dict):
        raise _InvalidArgumentError("payload_must_be_object")
    return obj


def _load_server_credentials(tls: TlsConfig) -> grpc.ServerCredentials:
    """Load TLS credentials for the gRPC server."""
    with open(tls.server_cert_path, "rb") as f:
        server_cert = f.read()
    with open(tls.server_key_path, "rb") as f:
        server_key = f.read()
    with open(tls.client_ca_path, "rb") as f:
        client_ca = f.read()
    return grpc.ssl_server_credentials(
        [(server_key, server_cert)],
        root_certificates=client_ca,
        require_client_auth=True,
    )


def _spiffe_agent_id(context: grpc_aio.ServicerContext) -> str:
    """Extract agent ID from mTLS SPIFFE SAN."""
    auth_ctx = context.auth_context() or {}
    sans = auth_ctx.get("x509_subject_alternative_name")
    if not sans:
        raise _UnauthenticatedError("missing_spiffe_san")

    spiffes: list[str] = []
    for raw in sans:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            text = str(raw)

        if text.startswith("spiffe://"):
            spiffes.append(text)

    if len(spiffes) != 1:
        raise _UnauthenticatedError("invalid_spiffe_san")

    m = _SPIFFE_RE.match(spiffes[0])
    if not m:
        raise _UnauthenticatedError("invalid_spiffe_format")

    return m.group("agent_id")


class _RpcError(Exception):
    """Base exception that maps to gRPC status codes."""

    def __init__(self, status: grpc.StatusCode, message: str):
        """Initialize error with gRPC status and message."""
        super().__init__(message)
        self.status = status
        self.message = message


class _UnauthenticatedError(_RpcError):
    """Unauthenticated gRPC error."""

    def __init__(self, message: str):
        """Initialize unauthenticated error."""
        super().__init__(grpc.StatusCode.UNAUTHENTICATED, message)


class _PermissionDeniedError(_RpcError):
    """Permission denied gRPC error."""

    def __init__(self, message: str):
        """Initialize permission denied error."""
        super().__init__(grpc.StatusCode.PERMISSION_DENIED, message)


class _ResourceExhaustedError(_RpcError):
    """Resource exhausted gRPC error."""

    def __init__(self, message: str):
        """Initialize resource exhausted error."""
        super().__init__(grpc.StatusCode.RESOURCE_EXHAUSTED, message)


class _InvalidArgumentError(_RpcError):
    """Invalid argument gRPC error."""

    def __init__(self, message: str):
        """Initialize invalid argument error."""
        super().__init__(grpc.StatusCode.INVALID_ARGUMENT, message)


class _FailedPreconditionError(_RpcError):
    """Failed precondition gRPC error."""

    def __init__(self, message: str):
        """Initialize failed precondition error."""
        super().__init__(grpc.StatusCode.FAILED_PRECONDITION, message)
