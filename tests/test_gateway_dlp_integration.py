"""Tests for DLP integration with Gateway Service."""

import pytest
import time
from redis.asyncio import Redis

from mas.agent import AgentMessage
from mas.gateway import GatewayService
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
async def gateway_with_dlp(redis):
    """Gateway service with DLP enabled."""
    settings = GatewaySettings(
        rate_limit=RateLimitSettings(per_minute=100, per_hour=1000),
        features=FeaturesSettings(
            dlp=True,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        ),
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()
    yield gateway
    await gateway.stop()


@pytest.fixture
async def gateway_without_dlp(redis):
    """Gateway service with DLP disabled."""
    settings = GatewaySettings(
        rate_limit=RateLimitSettings(per_minute=100, per_hour=1000),
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        ),
    )
    gateway = GatewayService(settings=settings)
    await gateway.start()
    yield gateway
    await gateway.stop()


async def setup_agents(redis, agent_a_id, agent_b_id, token_a):
    """Helper to set up test agents."""
    await redis.hset(
        f"agent:{agent_a_id}",
        mapping={
            "token": token_a,
            "status": "ACTIVE",
            "token_expires": str(time.time() + 3600),
        },
    )
    await redis.hset(
        f"agent:{agent_b_id}",
        mapping={
            "token": "token_b_123",
            "status": "ACTIVE",
            "token_expires": str(time.time() + 3600),
        },
    )


class TestGatewayDLPIntegration:
    """Test DLP integration with Gateway Service."""

    async def test_gateway_blocks_credit_card(self, gateway_with_dlp, redis):
        """Test that gateway blocks messages with credit card numbers."""
        # Setup agents
        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_with_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Create message with credit card (should be blocked)
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"card": "4532-0151-2345-6789", "message": "Payment info"},
        )

        result = await gateway_with_dlp.handle_message(message, token_a)

        assert result.success is False
        assert result.decision == "DLP_BLOCKED"
        assert "credit_card" in result.message

    async def test_gateway_blocks_aws_key(self, gateway_with_dlp, redis):
        """Test that gateway blocks messages with AWS keys."""
        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_with_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Create message with AWS key
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"config": "AWS key: AKIAIOSFODNN7EXAMPLE"},
        )

        result = await gateway_with_dlp.handle_message(message, token_a)

        assert result.success is False
        assert result.decision == "DLP_BLOCKED"
        assert "aws_key" in result.message

    async def test_gateway_redacts_ssn(self, gateway_with_dlp, redis):
        """Test that gateway redacts SSN (default policy)."""
        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_with_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Create message with SSN (default policy is REDACT)
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"ssn": "123-45-6789", "name": "John Doe"},
        )

        result = await gateway_with_dlp.handle_message(message, token_a)

        # Message should succeed (redacted, not blocked)
        assert result.success is True
        assert result.decision == "ALLOWED"

    async def test_gateway_allows_clean_message(self, gateway_with_dlp, redis):
        """Test that gateway allows clean messages through DLP."""
        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_with_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Create clean message
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"message": "Hello, how are you?", "count": 42},
        )

        result = await gateway_with_dlp.handle_message(message, token_a)

        assert result.success is True
        assert result.decision == "ALLOWED"

    async def test_gateway_dlp_disabled(self, gateway_without_dlp, redis):
        """Test that DLP can be disabled."""
        assert gateway_without_dlp.dlp is None

        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_without_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Message with credit card should pass (DLP disabled)
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"card": "4532-0151-2345-6789"},
        )

        result = await gateway_without_dlp.handle_message(message, token_a)

        assert result.success is True
        assert result.decision == "ALLOWED"

    async def test_dlp_violations_logged_in_audit(self, gateway_with_dlp, redis):
        """Test that DLP violations are logged in audit trail."""
        agent_a_id = "test_agent_a"
        agent_b_id = "test_agent_b"
        token_a = "token_a_123"

        await setup_agents(redis, agent_a_id, agent_b_id, token_a)
        await gateway_with_dlp.authz.set_permissions(agent_a_id, [agent_b_id])

        # Create message with violation
        message = AgentMessage(
            sender_id=agent_a_id,
            target_id=agent_b_id,
            payload={"card": "4532-0151-2345-6789"},
        )

        result = await gateway_with_dlp.handle_message(message, token_a)

        assert result.success is False
        assert result.decision == "DLP_BLOCKED"

        # Check audit log
        audit_messages = await redis.xrange("audit:messages", "-", "+")
        assert len(audit_messages) > 0

        # Find the DLP blocked message
        dlp_blocked = None
        for _, msg_data in audit_messages:
            if msg_data.get("decision") == "DLP_BLOCKED":
                dlp_blocked = msg_data
                break

        assert dlp_blocked is not None
        assert "credit_card" in dlp_blocked.get("violations", "")

        # Check security events
        security_events = await redis.xrange("audit:security_events", "-", "+")
        assert len(security_events) > 0

        # Find DLP violation event
        dlp_event = None
        for _, event_data in security_events:
            if event_data.get("event_type") == "DLP_VIOLATION":
                dlp_event = event_data
                break

        assert dlp_event is not None
