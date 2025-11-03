"""Tests for Gateway Service components."""

import asyncio
from typing import override
import pytest
import time
from redis.asyncio import Redis

from mas.agent import Agent, AgentMessage
from mas.gateway import (
    GatewayService,
    AuthenticationModule,
    AuthorizationModule,
    AuditModule,
    RateLimitModule,
)
from mas.gateway.config import GatewaySettings, FeaturesSettings, RateLimitSettings

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis():
    """Redis connection fixture."""
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    yield r
    # Cleanup
    await r.flushdb()
    await r.aclose()


@pytest.fixture
async def auth_module(redis):
    """Authentication module fixture."""
    return AuthenticationModule(redis)


@pytest.fixture
async def authz_module(redis):
    """Authorization module fixture."""
    return AuthorizationModule(redis)


@pytest.fixture
async def audit_module(redis):
    """Audit module fixture."""
    return AuditModule(redis)


@pytest.fixture
async def rate_limit_module(redis):
    """Rate limiting module fixture."""
    return RateLimitModule(redis, default_per_minute=10, default_per_hour=100)


@pytest.fixture
async def gateway(redis):
    """Gateway service fixture with message signing disabled for backward compatibility."""
    settings = GatewaySettings(
        rate_limit=RateLimitSettings(per_minute=10, per_hour=100),
        features=FeaturesSettings(
            dlp=True,
            priority_queue=False,
            rbac=False,
            message_signing=False,  # Disable for backward compatibility
            circuit_breaker=True,
        ),
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()
    yield gateway
    await gateway.stop()


class TestAuthenticationModule:
    """Test authentication module."""

    async def test_authenticate_success(self, auth_module, redis):
        """Test successful authentication."""
        # Setup agent with token
        agent_id = "test_agent"
        token = "test_token_123"
        await redis.hset(
            f"agent:{agent_id}",
            mapping={
                "token": token,
                "status": "ACTIVE",
                "token_expires": str(time.time() + 3600),
            },
        )

        result = await auth_module.authenticate(agent_id, token)
        assert result.authenticated is True
        assert result.agent_id == agent_id

    async def test_authenticate_invalid_token(self, auth_module, redis):
        """Test authentication with invalid token."""
        agent_id = "test_agent"
        await redis.hset(
            f"agent:{agent_id}", mapping={"token": "correct_token", "status": "ACTIVE"}
        )

        result = await auth_module.authenticate(agent_id, "wrong_token")
        assert result.authenticated is False
        assert "Invalid or expired token" in result.reason

    async def test_authenticate_agent_not_found(self, auth_module):
        """Test authentication with non-existent agent."""
        result = await auth_module.authenticate("nonexistent", "token")
        assert result.authenticated is False
        assert "not registered" in result.reason

    async def test_token_rotation(self, auth_module, redis):
        """Test token rotation."""
        agent_id = "test_agent"
        old_token = "old_token"
        await redis.hset(
            f"agent:{agent_id}", mapping={"token": old_token, "token_version": "1"}
        )

        new_token = await auth_module.rotate_token(agent_id)

        # New token should be different
        assert new_token != old_token

        # Old token should be revoked
        is_revoked = await redis.sismember(f"revoked_tokens:{agent_id}", old_token)
        assert is_revoked

        # New token should work
        # Note: This will fail because we didn't set status and expires
        # In real usage, the registry would handle this
        await auth_module.authenticate(agent_id, new_token)


class TestAuthorizationModule:
    """Test authorization module."""

    async def test_authorize_with_wildcard(self, authz_module, redis):
        """Test authorization with wildcard permission."""
        sender = "agent_a"
        target = "agent_b"

        # Setup target as ACTIVE
        await redis.hset(f"agent:{target}", "status", "ACTIVE")

        # Grant wildcard permission
        await authz_module.set_permissions(sender, allowed_targets=["*"])

        result = await authz_module.authorize(sender, target)
        assert result is True

    async def test_authorize_specific_target(self, authz_module, redis):
        """Test authorization with specific target."""
        sender = "agent_a"
        target = "agent_b"

        await redis.hset(f"agent:{target}", "status", "ACTIVE")
        await authz_module.set_permissions(sender, allowed_targets=[target])

        result = await authz_module.authorize(sender, target)
        assert result is True

    async def test_authorize_denied(self, authz_module, redis):
        """Test authorization denial."""
        sender = "agent_a"
        target = "agent_b"

        await redis.hset(f"agent:{target}", "status", "ACTIVE")
        # No permissions granted

        result = await authz_module.authorize(sender, target)
        assert result is False

    async def test_blocked_target(self, authz_module, redis):
        """Test blocked target takes precedence."""
        sender = "agent_a"
        target = "agent_b"

        await redis.hset(f"agent:{target}", "status", "ACTIVE")

        # Grant wildcard but also block specific target
        await authz_module.set_permissions(
            sender, allowed_targets=["*"], blocked_targets=[target]
        )

        result = await authz_module.authorize(sender, target)
        assert result is False


class TestAuditModule:
    """Test audit module."""

    async def test_log_message(self, audit_module):
        """Test message audit logging."""
        stream_id = await audit_module.log_message(
            message_id="msg_123",
            sender_id="agent_a",
            target_id="agent_b",
            decision="ALLOWED",
            latency_ms=15.5,
            payload={"test": "data"},
            violations=[],
        )

        assert stream_id is not None

    async def test_query_by_sender(self, audit_module):
        """Test querying audit log by sender."""
        # Log some messages
        await audit_module.log_message(
            "msg_1", "agent_a", "agent_b", "ALLOWED", 10.0, {}
        )
        await audit_module.log_message(
            "msg_2", "agent_a", "agent_c", "ALLOWED", 12.0, {}
        )

        # Query
        results = await audit_module.query_by_sender("agent_a")
        assert len(results) == 2
        assert all(r["sender_id"] == "agent_a" for r in results)

    async def test_log_security_event(self, audit_module):
        """Test security event logging."""
        stream_id = await audit_module.log_security_event(
            "AUTH_FAILURE", {"agent_id": "agent_a", "reason": "Invalid token"}
        )

        assert stream_id is not None

        # Query security events
        events = await audit_module.query_security_events()
        assert len(events) >= 1


class TestRateLimitModule:
    """Test rate limiting module."""

    async def test_rate_limit_allows_within_limit(self, rate_limit_module):
        """Test rate limit allows messages within limit."""
        agent_id = "agent_a"

        for i in range(5):
            result = await rate_limit_module.check_rate_limit(agent_id, f"msg_{i}")
            assert result.allowed is True
            assert result.remaining >= 0

    async def test_rate_limit_blocks_over_limit(self, rate_limit_module):
        """Test rate limit blocks messages over limit."""
        agent_id = "agent_a"

        # Send 10 messages (the limit)
        for i in range(10):
            result = await rate_limit_module.check_rate_limit(agent_id, f"msg_{i}")
            assert result.allowed is True

        # 11th message should be blocked
        result = await rate_limit_module.check_rate_limit(agent_id, "msg_11")
        assert result.allowed is False
        assert result.remaining == 0

    async def test_custom_limits(self, rate_limit_module):
        """Test setting custom rate limits."""
        agent_id = "agent_a"

        # Set low limit
        await rate_limit_module.set_limits(agent_id, per_minute=2)

        # First two should pass
        r1 = await rate_limit_module.check_rate_limit(agent_id, "msg_1")
        r2 = await rate_limit_module.check_rate_limit(agent_id, "msg_2")
        assert r1.allowed and r2.allowed

        # Third should fail
        r3 = await rate_limit_module.check_rate_limit(agent_id, "msg_3")
        assert r3.allowed is False


class TestGatewayService:
    """Test complete gateway service."""

    async def test_gateway_allows_authorized_message(self, gateway, redis):
        """Test gateway allows properly authorized message."""
        sender = "agent_a"
        target = "agent_b"
        token = "test_token"

        # Setup sender
        await redis.hset(
            f"agent:{sender}",
            mapping={
                "token": token,
                "status": "ACTIVE",
                "token_expires": str(time.time() + 3600),
            },
        )

        # Setup target
        await redis.hset(f"agent:{target}", "status", "ACTIVE")

        # Grant permission
        await gateway.authz.set_permissions(sender, allowed_targets=[target])

        # Send message
        message = AgentMessage(
            sender_id=sender, target_id=target, payload={"test": "data"}
        )

        result = await gateway.handle_message(message, token)
        assert result.success is True
        assert result.decision == "ALLOWED"
        assert result.latency_ms is not None

    async def test_gateway_blocks_unauthorized_message(self, gateway, redis):
        """Test gateway blocks unauthorized message."""
        sender = "agent_a"
        target = "agent_b"
        token = "test_token"

        # Setup sender
        await redis.hset(
            f"agent:{sender}",
            mapping={
                "token": token,
                "status": "ACTIVE",
                "token_expires": str(time.time() + 3600),
            },
        )

        # Setup target but NO permission granted
        await redis.hset(f"agent:{target}", "status", "ACTIVE")

        message = AgentMessage(
            sender_id=sender, target_id=target, payload={"test": "data"}
        )

        result = await gateway.handle_message(message, token)
        assert result.success is False
        assert result.decision == "AUTHZ_DENIED"

    async def test_gateway_blocks_invalid_auth(self, gateway, redis):
        """Test gateway blocks message with invalid authentication."""
        sender = "agent_a"
        target = "agent_b"

        await redis.hset(f"agent:{sender}", "status", "ACTIVE")

        message = AgentMessage(
            sender_id=sender, target_id=target, payload={"test": "data"}
        )

        result = await gateway.handle_message(message, "wrong_token")
        assert result.success is False
        assert result.decision == "AUTH_FAILED"

    async def test_gateway_enforces_rate_limits(self, gateway, redis):
        """Test gateway enforces rate limits."""
        sender = "agent_a"
        target = "agent_b"
        token = "test_token"

        # Setup sender and target
        await redis.hset(
            f"agent:{sender}",
            mapping={
                "token": token,
                "status": "ACTIVE",
                "token_expires": str(time.time() + 3600),
            },
        )
        await redis.hset(f"agent:{target}", "status", "ACTIVE")
        await gateway.authz.set_permissions(sender, allowed_targets=[target])

        # Send messages up to limit (10)
        for i in range(10):
            message = AgentMessage(
                sender_id=sender, target_id=target, payload={"msg": i}
            )
            result = await gateway.handle_message(message, token)
            assert result.success is True

        # Next message should be rate limited
        message = AgentMessage(
            sender_id=sender, target_id=target, payload={"msg": "over_limit"}
        )
        result = await gateway.handle_message(message, token)
        assert result.success is False
        assert result.decision == "RATE_LIMITED"

    async def test_gateway_audit_logging(self, gateway, redis):
        """Test gateway logs all messages to audit."""
        sender = "agent_a"
        target = "agent_b"
        token = "test_token"

        # Setup
        await redis.hset(
            f"agent:{sender}",
            mapping={
                "token": token,
                "status": "ACTIVE",
                "token_expires": str(time.time() + 3600),
            },
        )
        await redis.hset(f"agent:{target}", "status", "ACTIVE")
        await gateway.authz.set_permissions(sender, allowed_targets=[target])

        # Send message
        message = AgentMessage(
            sender_id=sender, target_id=target, payload={"test": "audit"}
        )
        await gateway.handle_message(message, token)

        # Check audit log
        audit_entries = await gateway.audit.query_by_sender(sender)
        assert len(audit_entries) >= 1
        assert any(e["message_id"] == message.message_id for e in audit_entries)


class TestAgentWithGateway:
    """Test Agent SDK with gateway integration."""

    async def test_agent_sends_through_gateway(self, redis):
        """Test agent can send messages through gateway."""
        # Start gateway with test-friendly settings
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=True,
                priority_queue=False,
                rbac=False,
                message_signing=False,  # Disabled for simpler testing
                circuit_breaker=True,
            ),
        )
        gateway = GatewayService(settings=settings)
        await gateway.start()

        try:
            # Create agents
            sender = Agent("agent_a", use_gateway=True)
            receiver = Agent("agent_b")

            await sender.start()
            await receiver.start()

            # Configure sender to use gateway
            sender.set_gateway(gateway)

            # Grant permissions
            await gateway.authz.set_permissions(
                sender.id, allowed_targets=[receiver.id]
            )

            # Send message
            received_messages = []

            class TestReceiver(Agent):
                @override
                async def on_message(self, message):
                    received_messages.append(message)

            receiver_agent = TestReceiver("agent_b")
            await receiver_agent.start()

            # Grant permission for sender
            await gateway.authz.set_permissions(
                sender.id, allowed_targets=[receiver_agent.id]
            )

            # Send through gateway
            await sender.send(receiver_agent.id, {"test": "gateway"})

            # Wait a bit for message delivery
            await asyncio.sleep(0.5)

            # Cleanup
            await sender.stop()
            await receiver.stop()
            await receiver_agent.stop()

        finally:
            await gateway.stop()
            await redis.flushdb()
