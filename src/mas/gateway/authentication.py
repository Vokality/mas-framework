"""Authentication Module for Gateway Service."""

import hashlib
import logging
import secrets
from typing import Optional

from pydantic import BaseModel

from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


class AuthResult(BaseModel):
    """Authentication result."""

    authenticated: bool
    agent_id: Optional[str] = None
    reason: Optional[str] = None


class AuthenticationModule:
    """
    Authentication module for validating agent tokens.

    Implements instance-scoped bearer token authentication:
    - Each agent instance has its own token.
    - Redis stores only a SHA-256 hash of the token.
    - The gateway authenticates using (agent_id, sender_instance_id, token).

    Redis Data Model:
        agent:{agent_id} → hash with "status" and metadata
        agent:{agent_id}:token_hash:{instance_id} → string (sha256 hex)
    """

    def __init__(self, redis: AsyncRedisProtocol):
        """
        Initialize authentication module.

        Args:
            redis: Redis connection
        """
        self.redis: AsyncRedisProtocol = redis

    async def authenticate(
        self, agent_id: str, token: str, *, sender_instance_id: str | None
    ) -> AuthResult:
        """
        Authenticate agent by validating token.

        Args:
            agent_id: Agent identifier from message
            token: Authentication token from message
            sender_instance_id: Instance identifier from message metadata

        Returns:
            AuthResult with authentication status
        """
        if not agent_id or not token:
            return AuthResult(authenticated=False, reason="missing_agent_id_or_token")

        if not sender_instance_id:
            return AuthResult(
                authenticated=False,
                agent_id=agent_id,
                reason="missing_sender_instance_id",
            )

        # Check if agent exists
        agent_key = f"agent:{agent_id}"
        exists = await self.redis.exists(agent_key)
        if not exists:
            logger.warning(
                "Authentication failed: agent not found", extra={"agent_id": agent_id}
            )
            return AuthResult(
                authenticated=False, agent_id=agent_id, reason="Agent not registered"
            )

        # Validate token hash for this instance
        valid = await self.validate_token_hash(agent_id, sender_instance_id, token)
        if not valid:
            logger.warning(
                "Authentication failed: invalid token", extra={"agent_id": agent_id}
            )
            return AuthResult(
                authenticated=False,
                agent_id=agent_id,
                reason="invalid_token",
            )

        # Check agent status
        status = await self.redis.hget(agent_key, "status")
        if status != "ACTIVE":
            logger.warning(
                "Authentication failed: agent not active",
                extra={"agent_id": agent_id, "status": status},
            )
            return AuthResult(
                authenticated=False,
                agent_id=agent_id,
                reason="agent_not_active",
            )

        logger.debug("Authentication successful", extra={"agent_id": agent_id})
        return AuthResult(authenticated=True, agent_id=agent_id)

    async def validate_token_hash(
        self, agent_id: str, sender_instance_id: str, token: str
    ) -> bool:
        """Validate token hash for a specific instance."""
        token_hash_key = f"agent:{agent_id}:token_hash:{sender_instance_id}"
        stored_hash = await self.redis.get(token_hash_key)
        if not stored_hash:
            return False

        computed = hashlib.sha256(token.encode()).hexdigest()
        return secrets.compare_digest(computed, stored_hash)
