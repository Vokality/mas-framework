"""Authentication Module for Gateway Service."""

import logging
import secrets
import time
from typing import Optional
from mas.redis_types import AsyncRedisProtocol
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AuthResult(BaseModel):
    """Authentication result."""

    authenticated: bool
    agent_id: Optional[str] = None
    reason: Optional[str] = None


class AuthenticationModule:
    """
    Authentication module for validating agent tokens.

    Implements Phase 1 token-based authentication as per GATEWAY.md:
    - Validates tokens against Redis registry
    - Checks token expiration
    - Supports token rotation
    - Maintains revocation list

    Redis Data Model:
        agent:{agent_id} → hash with "token", "token_expires", "token_version"
        revoked_tokens:{agent_id} → set of revoked tokens (TTL: 24h)
    """

    def __init__(self, redis: AsyncRedisProtocol, token_lifetime: int = 86400):
        """
        Initialize authentication module.

        Args:
            redis: Redis connection
            token_lifetime: Token lifetime in seconds (default 24 hours)
        """
        self.redis: AsyncRedisProtocol = redis
        self.token_lifetime = token_lifetime

    async def authenticate(self, agent_id: str, token: str) -> AuthResult:
        """
        Authenticate agent by validating token.

        Args:
            agent_id: Agent identifier from message
            token: Authentication token from message

        Returns:
            AuthResult with authentication status
        """
        if not agent_id or not token:
            return AuthResult(authenticated=False, reason="Missing agent_id or token")

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

        # Validate token
        valid = await self.validate_token(agent_id, token)
        if not valid:
            logger.warning(
                "Authentication failed: invalid token", extra={"agent_id": agent_id}
            )
            return AuthResult(
                authenticated=False,
                agent_id=agent_id,
                reason="Invalid or expired token",
            )

        # Check agent status
        status = await self.redis.hget(agent_key, "status")
        if status != "ACTIVE":
            logger.warning(
                "Authentication failed: agent not active",
                extra={"agent_id": agent_id, "status": status},
            )
            return AuthResult(
                authenticated=False, agent_id=agent_id, reason=f"Agent status: {status}"
            )

        logger.debug("Authentication successful", extra={"agent_id": agent_id})
        return AuthResult(authenticated=True, agent_id=agent_id)

    async def validate_token(self, agent_id: str, token: str) -> bool:
        """
        Validate token against stored value.

        Args:
            agent_id: Agent identifier
            token: Token to validate

        Returns:
            True if token is valid and not expired
        """
        agent_key = f"agent:{agent_id}"

        # Check revocation list first
        revoked_key = f"revoked_tokens:{agent_id}"
        is_revoked = await self.redis.sismember(revoked_key, token)
        if is_revoked:
            logger.warning("Token is revoked", extra={"agent_id": agent_id})
            return False

        # Get stored token
        stored_token = await self.redis.hget(agent_key, "token")
        if not stored_token:
            return False

        # Compare tokens
        if token != stored_token:
            return False

        # Check expiration
        expires_str = await self.redis.hget(agent_key, "token_expires")
        if expires_str:
            try:
                expires = float(expires_str)
                if time.time() > expires:
                    logger.warning("Token expired", extra={"agent_id": agent_id})
                    return False
            except ValueError:
                logger.error(
                    "Invalid token_expires value", extra={"agent_id": agent_id}
                )
                return False

        return True

    async def rotate_token(self, agent_id: str) -> str:
        """
        Rotate agent token.

        Args:
            agent_id: Agent identifier

        Returns:
            New token
        """
        agent_key = f"agent:{agent_id}"

        # Get old token for revocation
        old_token = await self.redis.hget(agent_key, "token")

        # Generate new token
        new_token = secrets.token_urlsafe(32)
        new_expires = time.time() + self.token_lifetime

        # Get current version and increment
        version_str = await self.redis.hget(agent_key, "token_version")
        version = int(version_str) + 1 if version_str else 1

        # Update token
        await self.redis.hset(
            agent_key,
            mapping={
                "token": new_token,
                "token_expires": str(new_expires),
                "token_version": str(version),
            },
        )

        # Add old token to revocation list
        if old_token:
            revoked_key = f"revoked_tokens:{agent_id}"
            await self.redis.sadd(revoked_key, old_token)
            # Set TTL to token lifetime (24 hours)
            await self.redis.expire(revoked_key, self.token_lifetime)

        logger.info("Token rotated", extra={"agent_id": agent_id, "version": version})

        return new_token

    async def revoke_token(self, agent_id: str) -> None:
        """
        Immediately revoke agent's current token.

        Args:
            agent_id: Agent identifier
        """
        agent_key = f"agent:{agent_id}"

        # Get current token
        token = await self.redis.hget(agent_key, "token")
        if not token:
            return

        # Add to revocation list
        revoked_key = f"revoked_tokens:{agent_id}"
        await self.redis.sadd(revoked_key, token)
        await self.redis.expire(revoked_key, self.token_lifetime)

        # Clear token from agent record
        await self.redis.hdel(agent_key, "token", "token_expires")

        logger.info("Token revoked", extra={"agent_id": agent_id})
