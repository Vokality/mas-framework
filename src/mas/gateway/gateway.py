"""Gateway Service - Central message validation and routing."""
import asyncio
import logging
import time
from typing import Optional
from redis.asyncio import Redis
from pydantic import BaseModel

from ..agent import AgentMessage
from .authentication import AuthenticationModule
from .authorization import AuthorizationModule
from .audit import AuditModule
from .rate_limit import RateLimitModule

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

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        rate_limit_per_minute: int = 100,
        rate_limit_per_hour: int = 1000,
    ):
        """
        Initialize gateway service.

        Args:
            redis_url: Redis connection URL
            rate_limit_per_minute: Default rate limit per minute
            rate_limit_per_hour: Default rate limit per hour
        """
        self.redis_url = redis_url
        self._redis: Optional[Redis] = None
        self._running = False

        # Modules (initialized in start())
        self._auth: Optional[AuthenticationModule] = None
        self._authz: Optional[AuthorizationModule] = None
        self._audit: Optional[AuditModule] = None
        self._rate_limit: Optional[RateLimitModule] = None

        # Configuration
        self.rate_limit_per_minute = rate_limit_per_minute
        self.rate_limit_per_hour = rate_limit_per_hour

    async def start(self) -> None:
        """Start the gateway service."""
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)

        # Initialize modules
        self._auth = AuthenticationModule(self._redis)
        self._authz = AuthorizationModule(self._redis)
        self._audit = AuditModule(self._redis)
        self._rate_limit = RateLimitModule(
            self._redis,
            default_per_minute=self.rate_limit_per_minute,
            default_per_hour=self.rate_limit_per_hour
        )

        self._running = True
        logger.info("Gateway Service started", extra={"redis_url": self.redis_url})

    async def stop(self) -> None:
        """Stop the gateway service."""
        self._running = False

        if self._redis:
            await self._redis.aclose()

        logger.info("Gateway Service stopped")

    async def handle_message(self, message: AgentMessage, token: str) -> GatewayResult:
        """
        Handle message through gateway validation pipeline.

        Pipeline stages:
        1. Authentication - Validate sender token
        2. Authorization - Check sender can message target
        3. Rate Limiting - Check sender within limits
        4. Audit Logging - Log message and decision (async)
        5. Routing - Publish to target's stream

        Args:
            message: Agent message to process
            token: Sender's authentication token

        Returns:
            GatewayResult with processing outcome
        """
        if not self._running:
            return GatewayResult(
                success=False,
                decision="SERVICE_UNAVAILABLE",
                message="Gateway service not running"
            )

        start_time = time.time()
        violations = []

        # Stage 1: Authentication
        auth_result = await self._auth.authenticate(message.sender_id, token)
        if not auth_result.authenticated:
            latency_ms = (time.time() - start_time) * 1000

            # Log security event
            await self._audit.log_security_event(
                "AUTH_FAILURE",
                {
                    "sender_id": message.sender_id,
                    "target_id": message.target_id,
                    "reason": auth_result.reason
                }
            )

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "AUTH_FAILED",
                latency_ms,
                message.payload,
                violations=["authentication_failure"]
            )

            return GatewayResult(
                success=False,
                decision="AUTH_FAILED",
                message=auth_result.reason,
                latency_ms=latency_ms
            )

        # Stage 2: Authorization
        authorized = await self._authz.authorize(
            message.sender_id,
            message.target_id,
            action="send"
        )
        if not authorized:
            latency_ms = (time.time() - start_time) * 1000

            # Log security event
            await self._audit.log_security_event(
                "AUTHZ_DENIED",
                {
                    "sender_id": message.sender_id,
                    "target_id": message.target_id
                }
            )

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "AUTHZ_DENIED",
                latency_ms,
                message.payload,
                violations=["authorization_denied"]
            )

            return GatewayResult(
                success=False,
                decision="AUTHZ_DENIED",
                message="Not authorized to message target",
                latency_ms=latency_ms
            )

        # Stage 3: Rate Limiting
        rate_result = await self._rate_limit.check_rate_limit(
            message.sender_id,
            message.message_id
        )
        if not rate_result.allowed:
            latency_ms = (time.time() - start_time) * 1000

            # Log to audit
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "RATE_LIMITED",
                latency_ms,
                message.payload,
                violations=["rate_limit_exceeded"]
            )

            return GatewayResult(
                success=False,
                decision="RATE_LIMITED",
                message=f"Rate limit exceeded. Reset at {rate_result.reset_time}",
                latency_ms=latency_ms
            )

        # All checks passed - route message
        try:
            await self._route_message(message)
            latency_ms = (time.time() - start_time) * 1000

            # Log successful delivery
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "ALLOWED",
                latency_ms,
                message.payload,
                violations=violations
            )

            logger.info(
                "Message routed",
                extra={
                    "message_id": message.message_id,
                    "sender": message.sender_id,
                    "target": message.target_id,
                    "latency_ms": latency_ms
                }
            )

            return GatewayResult(
                success=True,
                decision="ALLOWED",
                message="Message delivered",
                latency_ms=latency_ms
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000

            logger.error(
                "Failed to route message",
                exc_info=e,
                extra={
                    "message_id": message.message_id,
                    "sender": message.sender_id,
                    "target": message.target_id
                }
            )

            # Log failure
            await self._audit.log_message(
                message.message_id,
                message.sender_id,
                message.target_id,
                "ROUTING_FAILED",
                latency_ms,
                message.payload,
                violations=["routing_error"]
            )

            return GatewayResult(
                success=False,
                decision="ROUTING_FAILED",
                message=str(e),
                latency_ms=latency_ms
            )

    async def _route_message(self, message: AgentMessage) -> None:
        """
        Route message to target agent's stream.

        Uses Redis Streams for reliable delivery (at-least-once).

        Args:
            message: Message to route
        """
        # For MVP, we'll use the same pub/sub system as before
        # In future phases, we'll switch to Redis Streams
        target_channel = f"agent.{message.target_id}"

        await self._redis.publish(
            target_channel,
            message.model_dump_json()
        )

        logger.debug(
            "Message published",
            extra={
                "message_id": message.message_id,
                "target_channel": target_channel
            }
        )

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

    async def get_stats(self) -> dict:
        """
        Get gateway statistics.

        Returns:
            Dictionary with gateway stats
        """
        audit_stats = await self._audit.get_stats()

        return {
            "status": "running" if self._running else "stopped",
            "audit": audit_stats,
        }
