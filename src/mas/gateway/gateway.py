"""Gateway Service - Central message validation and routing."""

import logging
import time
from typing import Optional, cast

from pydantic import BaseModel
from redis.asyncio import Redis

from ..agent import AgentMessage
from .audit import AuditModule
from .auth_manager import AuthorizationManager
from .authentication import AuthenticationModule
from .authorization import AuthorizationModule
from .circuit_breaker import CircuitBreakerConfig, CircuitBreakerModule
from .config import GatewaySettings
from .dlp import ActionPolicy, DLPModule
from .message_signing import MessageSigningModule
from .metrics import MetricsCollector
from .priority_queue import MessagePriority, PriorityQueueModule
from .rate_limit import RateLimitModule
from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


class GatewayResult(BaseModel):
    """Gateway processing result."""

    success: bool
    decision: str  # ALLOWED, AUTH_FAILED, AUTHZ_DENIED, RATE_LIMITED, etc.
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class GatewayService:
    """
    Gateway Service for centralized message validation and routing.

    Implements the Gateway Pattern as per GATEWAY.md:
    - Authentication (token validation)
    - Authorization (ACL enforcement)
    - Rate limiting (token bucket)
    - Audit logging (Redis Streams)
    - Message routing to Redis Streams

    Message Flow:
    1. Authentication - Validate token
    2. Authorization - Check ACL permissions
    3. Rate Limiting - Check limits
    4. Audit Logging - Log decision
    5. Routing - Deliver to target stream

    Usage:
        gateway = GatewayService(redis_url="redis://localhost:6379")
        await gateway.start()
        result = await gateway.handle_message(message)
    """

    def __init__(self, settings: Optional[GatewaySettings] = None):
        """
        Initialize gateway service.

        Args:
            settings: GatewaySettings configuration object.
                     If None, uses production-ready defaults (all security features enabled).

        Example:
            # Use production defaults
            gateway = GatewayService()

            # Custom configuration
            gateway = GatewayService(
                settings=GatewaySettings(
                    redis=RedisSettings(url="redis://prod:6379"),
                    features=FeaturesSettings(message_signing=False),
                )
            )

            # From config file
            settings = GatewaySettings.from_yaml("gateway.yaml")
            gateway = GatewayService(settings=settings)
        """
        self.settings = settings or GatewaySettings()
        self._redis: Optional[Redis] = None
        self._running = False

        # Modules (initialized in start())
        self._auth: Optional[AuthenticationModule] = None
        self._authz: Optional[AuthorizationModule] = None
        self._audit: Optional[AuditModule] = None
        self._rate_limit: Optional[RateLimitModule] = None
        self._dlp: Optional[DLPModule] = None
        self._priority_queue: Optional[PriorityQueueModule] = None
        self._message_signing: Optional[MessageSigningModule] = None
        self._circuit_breaker: Optional[CircuitBreakerModule] = None

    async def start(self) -> None:
        """Start the gateway service."""
        # Initialize Redis connection
        self._redis = Redis.from_url(
            self.settings.redis.url,
            decode_responses=self.settings.redis.decode_responses,
            socket_timeout=self.settings.redis.socket_timeout,
        )

        # Initialize core modules (always enabled)
        self._auth = AuthenticationModule(cast(AsyncRedisProtocol, self._redis))
        self._authz = AuthorizationModule(
            cast(AsyncRedisProtocol, self._redis),
            enable_rbac=self.settings.features.rbac,
        )
        self._audit = AuditModule(cast(AsyncRedisProtocol, self._redis))
        self._rate_limit = RateLimitModule(
            cast(AsyncRedisProtocol, self._redis),
            default_per_minute=self.settings.rate_limit.per_minute,
            default_per_hour=self.settings.rate_limit.per_hour,
        )

        # Initialize optional modules based on feature flags
        if self.settings.features.dlp:
            self._dlp = DLPModule()

        if self.settings.features.priority_queue:
            self._priority_queue = PriorityQueueModule(self._redis)

        if self.settings.features.message_signing:
            self._message_signing = MessageSigningModule(
                self._redis,
                max_timestamp_drift=self.settings.message_signing.max_timestamp_drift,
                nonce_ttl=self.settings.message_signing.nonce_ttl,
            )

        if self.settings.features.circuit_breaker:
            cb_config = CircuitBreakerConfig(
                failure_threshold=self.settings.circuit_breaker.failure_threshold,
                success_threshold=self.settings.circuit_breaker.success_threshold,
                timeout_seconds=self.settings.circuit_breaker.timeout_seconds,
                window_seconds=self.settings.circuit_breaker.window_seconds,
            )
            self._circuit_breaker = CircuitBreakerModule(self._redis, config=cb_config)

        # Initialize metrics
        MetricsCollector.set_gateway_info(
            version="0.1.14",
            dlp_enabled=self.settings.features.dlp,
            priority_queue_enabled=self.settings.features.priority_queue,
        )

        self._running = True
        logger.info(
            "Gateway Service started",
            extra={
                "redis_url": self.settings.redis.url,
                "features": {
                    "dlp": self.settings.features.dlp,
                    "priority_queue": self.settings.features.priority_queue,
                    "rbac": self.settings.features.rbac,
                    "message_signing": self.settings.features.message_signing,
                    "circuit_breaker": self.settings.features.circuit_breaker,
                },
            },
        )

    async def stop(self) -> None:
        """Stop the gateway service."""
        self._running = False

        if self._redis:
            await self._redis.aclose()

        logger.info("Gateway Service stopped")

    async def handle_message(
        self,
        message: AgentMessage,
        token: str,
        signature: Optional[str] = None,
        timestamp: Optional[float] = None,
        nonce: Optional[str] = None,
    ) -> GatewayResult:
        """
        Handle message through gateway validation pipeline.

        Pipeline stages:
        1. Authentication - Validate sender token
        2. Message Signing - Verify signature (if enabled)
        3. Authorization - Check sender can message target (ACL + RBAC)
        4. Rate Limiting - Check sender within limits
        5. DLP Scanning - Scan for sensitive data (if enabled)
        6. Audit Logging - Log message and decision (async)
        7. Routing - Publish to target's stream

        Args:
            message: Agent message to process
            token: Sender's authentication token
            signature: Message HMAC signature (required if message_signing enabled)
            timestamp: Message timestamp (required if message_signing enabled)
            nonce: Message nonce (required if message_signing enabled)

        Returns:
            GatewayResult with processing outcome
        """
        if not self._running:
            return GatewayResult(
                success=False,
                decision="SERVICE_UNAVAILABLE",
                message="Gateway service not running",
            )

        start_time = time.time()
        violations = []

        # Ensure modules are initialized
        assert self._auth is not None, "Gateway not started"
        assert self._authz is not None, "Gateway not started"
        assert self._audit is not None, "Gateway not started"
        assert self._rate_limit is not None, "Gateway not started"
        assert self._redis is not None, "Gateway not started"

        # Stage 1: Authentication
        auth_result = await self._auth.authenticate(message.sender_id, token)  # type: ignore[union-attr]
        if not auth_result.authenticated:
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            MetricsCollector.record_auth_failure(auth_result.reason or "unknown")
            MetricsCollector.record_message("AUTH_FAILED", latency_ms / 1000)

            # Log security event
            await self._audit.log_security_event(
                "AUTH_FAILURE",
                {
                    "sender_id": message.sender_id,
                    "target_id": message.target_id,
                    "reason": auth_result.reason,
                },
            )

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "AUTH_FAILED",
                latency_ms,
                message.payload,
                violations=["authentication_failure"],
            )

            return GatewayResult(
                success=False,
                decision="AUTH_FAILED",
                message=auth_result.reason,
                latency_ms=latency_ms,
            )

        # Stage 2: Message Signing Verification (if enabled)
        if self.settings.features.message_signing and self._message_signing:
            # Verify required signature parameters are present
            if not signature or timestamp is None or not nonce:
                latency_ms = (time.time() - start_time) * 1000

                # Record metrics
                MetricsCollector.record_auth_failure("missing_signature_parameters")
                MetricsCollector.record_message("SIGNATURE_INVALID", latency_ms / 1000)

                # Log security event
                await self._audit.log_security_event(
                    "SIGNATURE_INVALID",
                    {
                        "sender_id": message.sender_id,
                        "target_id": message.target_id,
                        "reason": "missing_signature_parameters",
                    },
                )

                # Log to audit
                await self._audit.log_message(
                    message.message_id,
                    message.sender_id,
                    message.target_id,
                    "SIGNATURE_INVALID",
                    latency_ms,
                    message.payload,
                    violations=["missing_signature_parameters"],
                )

                return GatewayResult(
                    success=False,
                    decision="SIGNATURE_INVALID",
                    message="Message signing enabled but signature parameters missing",
                    latency_ms=latency_ms,
                )

            # Verify signature
            sig_result = await self._message_signing.verify_signature(
                agent_id=message.sender_id,
                message_id=message.message_id,
                payload=message.payload,
                signature=signature,
                timestamp=timestamp,
                nonce=nonce,
            )

            if not sig_result.valid:
                latency_ms = (time.time() - start_time) * 1000

                # Record metrics
                MetricsCollector.record_auth_failure(
                    sig_result.reason or "invalid_signature"
                )
                MetricsCollector.record_message("SIGNATURE_INVALID", latency_ms / 1000)

                # Log security event
                await self._audit.log_security_event(
                    "SIGNATURE_INVALID",
                    {
                        "sender_id": message.sender_id,
                        "target_id": message.target_id,
                        "reason": sig_result.reason,
                    },
                )

                # Log to audit
                await self._audit.log_message(
                    message.message_id,
                    message.sender_id,
                    message.target_id,
                    "SIGNATURE_INVALID",
                    latency_ms,
                    message.payload,
                    violations=["signature_verification_failed"],
                )

                return GatewayResult(
                    success=False,
                    decision="SIGNATURE_INVALID",
                    message=sig_result.reason or "Invalid message signature",
                    latency_ms=latency_ms,
                )

        # Stage 3: Authorization (ACL + RBAC)
        authorized = await self._authz.authorize(
            message.sender_id, message.target_id, action="send"
        )
        if not authorized:
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            MetricsCollector.record_authz_denied(message.sender_id, message.target_id)
            MetricsCollector.record_message("AUTHZ_DENIED", latency_ms / 1000)

            # Log security event
            await self._audit.log_security_event(
                "AUTHZ_DENIED",
                {"sender_id": message.sender_id, "target_id": message.target_id},
            )

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "AUTHZ_DENIED",
                latency_ms,
                message.payload,
                violations=["authorization_denied"],
            )

            return GatewayResult(
                success=False,
                decision="AUTHZ_DENIED",
                message="Not authorized to message target",
                latency_ms=latency_ms,
            )

        # Stage 4: Rate Limiting
        rate_result = await self._rate_limit.check_rate_limit(
            message.sender_id, message.message_id
        )
        if not rate_result.allowed:
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            MetricsCollector.record_rate_limited(message.sender_id)
            MetricsCollector.record_message("RATE_LIMITED", latency_ms / 1000)

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "RATE_LIMITED",
                latency_ms,
                message.payload,
                violations=["rate_limit_exceeded"],
            )

            return GatewayResult(
                success=False,
                decision="RATE_LIMITED",
                message=f"Rate limit exceeded. Reset at {rate_result.reset_time}",
                latency_ms=latency_ms,
            )

        # Stage 5: DLP Scanning (if enabled)
        if self.settings.features.dlp and self._dlp:
            scan_result = await self._dlp.scan(message.payload)

            if not scan_result.clean:
                violations.extend(
                    [v.violation_type.value for v in scan_result.violations]
                )

                if scan_result.action == ActionPolicy.BLOCK:
                    latency_ms = (time.time() - start_time) * 1000

                    # Record metrics
                    for violation in scan_result.violations:
                        MetricsCollector.record_dlp_violation(
                            violation.violation_type.value, "BLOCK"
                        )
                    MetricsCollector.record_message("DLP_BLOCKED", latency_ms / 1000)

                    # Log security event
                    await self._audit.log_security_event(
                        "DLP_VIOLATION",
                        {
                            "sender_id": message.sender_id,
                            "target_id": message.target_id,
                            "violations": [
                                v.violation_type.value for v in scan_result.violations
                            ],
                            "severity": [v.severity for v in scan_result.violations],
                        },
                    )

                    # Log to audit
                    await self._audit.log_message(
                        message.message_id,
                        message.sender_id,
                        message.target_id,
                        "DLP_BLOCKED",
                        latency_ms,
                        message.payload,
                        violations=violations,
                    )

                    return GatewayResult(
                        success=False,
                        decision="DLP_BLOCKED",
                        message=f"Message blocked due to DLP violations: {', '.join(violations)}",
                        latency_ms=latency_ms,
                    )

                elif (
                    scan_result.action == ActionPolicy.REDACT
                    and scan_result.redacted_payload
                ):
                    # Replace payload with redacted version
                    message.payload = scan_result.redacted_payload
                    logger.info(
                        "Message redacted by DLP",
                        extra={
                            "message_id": message.message_id,
                            "violations": violations,
                        },
                    )

        # All checks passed - route message
        try:
            await self._route_message(message)
            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            MetricsCollector.record_message("ALLOWED", latency_ms / 1000)

            # Log successful delivery
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "ALLOWED",
                latency_ms,
                message.payload,
                violations=violations,
            )

            logger.info(
                "Message routed",
                extra={
                    "message_id": message.message_id,
                    "sender": message.sender_id,
                    "target": message.target_id,
                    "latency_ms": latency_ms,
                },
            )

            return GatewayResult(
                success=True,
                decision="ALLOWED",
                message="Message delivered",
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000

            logger.error(
                "Failed to route message",
                exc_info=e,
                extra={
                    "message_id": message.message_id,
                    "sender": message.sender_id,
                    "target": message.target_id,
                },
            )

            # Log failure
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "ROUTING_FAILED",
                latency_ms,
                message.payload,
                violations=["routing_error"],
            )

            return GatewayResult(
                success=False,
                decision="ROUTING_FAILED",
                message=str(e),
                latency_ms=latency_ms,
            )

    async def _route_message(self, message: AgentMessage) -> None:
        """
        Route message to target agent's stream.

        If priority queue is enabled, enqueues message by priority.
        Otherwise, uses direct pub/sub for immediate delivery.

        Args:
            message: Message to route
        """
        if self.settings.features.priority_queue and self._priority_queue:
            # Determine priority from message metadata
            priority = self._determine_message_priority(message)

            # Enqueue message
            await self._priority_queue.enqueue(
                message_id=message.message_id,
                sender_id=message.sender_id,
                target_id=message.target_id,
                payload=message.payload,
                priority=priority,
            )

            logger.debug(
                "Message enqueued",
                extra={
                    "message_id": message.message_id,
                    "priority": priority.name,
                },
            )
        else:
            # Direct delivery via pub/sub (MVP)
            target_channel = f"agent.{message.target_id}"
            await self._redis.publish(target_channel, message.model_dump_json())  # type: ignore[union-attr]

            logger.debug(
                "Message published",
                extra={
                    "message_id": message.message_id,
                    "target_channel": target_channel,
                },
            )

    def _determine_message_priority(self, message: AgentMessage) -> MessagePriority:
        """
        Determine message priority based on payload markers.

        Priority rules:
        - payload.priority = "critical" → CRITICAL
        - payload.priority = "high" → HIGH
        - payload.priority = "low" → LOW
        - payload.priority = "bulk" → BULK
        - Default → NORMAL

        Args:
            message: Agent message

        Returns:
            MessagePriority enum
        """
        priority_str = str(message.payload.get("priority", "normal")).lower()

        priority_map = {
            "critical": MessagePriority.CRITICAL,
            "high": MessagePriority.HIGH,
            "normal": MessagePriority.NORMAL,
            "low": MessagePriority.LOW,
            "bulk": MessagePriority.BULK,
        }

        return priority_map.get(priority_str, MessagePriority.NORMAL)

    # Module accessors for management operations

    @property
    def auth(self) -> AuthenticationModule:
        """Get authentication module."""
        if not self._auth:
            raise RuntimeError("Gateway not started")
        return self._auth

    @property
    def authz(self) -> AuthorizationModule:
        """Get authorization module."""
        if not self._authz:
            raise RuntimeError("Gateway not started")
        return self._authz

    @property
    def audit(self) -> AuditModule:
        """Get audit module."""
        if not self._audit:
            raise RuntimeError("Gateway not started")
        return self._audit

    @property
    def rate_limit(self) -> RateLimitModule:
        """Get rate limiting module."""
        if not self._rate_limit:
            raise RuntimeError("Gateway not started")
        return self._rate_limit

    @property
    def dlp(self) -> DLPModule | None:
        """Get DLP module (if enabled)."""
        return self._dlp

    @property
    def priority_queue(self) -> PriorityQueueModule | None:
        """Get priority queue module (if enabled)."""
        return self._priority_queue

    @property
    def message_signing(self) -> MessageSigningModule | None:
        """Get message signing module (if enabled)."""
        return self._message_signing

    @property
    def circuit_breaker(self) -> CircuitBreakerModule | None:
        """Get circuit breaker module (if enabled)."""
        return self._circuit_breaker

    def auth_manager(self) -> "AuthorizationManager":
        """
        Get high-level authorization manager for easy configuration.

        Returns:
            AuthorizationManager instance

        Example:
            auth = gateway.auth_manager()
            await auth.allow_bidirectional("agent1", "agent2")
        """

        return AuthorizationManager(self)

    async def get_stats(self) -> dict:
        """
        Get gateway statistics.

        Returns:
            Dictionary with gateway stats
        """
        audit_stats = await self._audit.get_stats()  # type: ignore[union-attr]

        return {
            "audit": audit_stats,
            "status": "running" if self._running else "stopped",
        }
