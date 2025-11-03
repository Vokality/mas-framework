"""Authorization Module for Gateway Service."""

import logging
from typing import Optional
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class AuthorizationModule:
    """
    Authorization module for enforcing access control.

    Implements Phase 1 ACL (Access Control List) as per GATEWAY.md:
    - Simple allow-list per agent
    - Wildcard support ("*" allows all)
    - Block-list takes precedence over allow-list
    - Default deny (explicit allow required)

    Redis Data Model:
        agent:{agent_id}:allowed_targets → Set of allowed target IDs
        agent:{agent_id}:blocked_targets → Set of blocked target IDs
    """

    def __init__(self, redis: Redis):
        """
        Initialize authorization module.

        Args:
            redis: Redis connection
        """
        self.redis = redis

    async def authorize(
        self, sender_id: str, target_id: str, action: str = "send"
    ) -> bool:
        """
        Authorize message from sender to target.

        Args:
            sender_id: Sending agent ID
            target_id: Target agent ID
            action: Action type (currently only "send" supported)

        Returns:
            True if authorized, False otherwise
        """
        # Check ACL
        allowed = await self.check_acl(sender_id, target_id)

        if allowed:
            logger.debug(
                "Authorization granted",
                extra={"sender": sender_id, "target": target_id, "action": action},
            )
        else:
            logger.warning(
                "Authorization denied",
                extra={"sender": sender_id, "target": target_id, "action": action},
            )

        return allowed

    async def check_acl(self, sender_id: str, target_id: str) -> bool:
        """
        Check ACL permissions.

        Args:
            sender_id: Sending agent ID
            target_id: Target agent ID

        Returns:
            True if sender is allowed to message target
        """
        # Check if target exists and is active
        target_key = f"agent:{target_id}"
        exists = await self.redis.exists(target_key)
        if not exists:
            logger.warning("Target agent not found", extra={"target": target_id})
            return False

        status = await self.redis.hget(target_key, "status")
        if status != "ACTIVE":
            logger.warning(
                "Target agent not active", extra={"target": target_id, "status": status}
            )
            return False

        # Check blocked list first (takes precedence)
        blocked_key = f"agent:{sender_id}:blocked_targets"
        is_blocked = await self.redis.sismember(blocked_key, target_id)
        if is_blocked:
            logger.debug(
                "Target is blocked", extra={"sender": sender_id, "target": target_id}
            )
            return False

        # Check allowed list
        allowed_key = f"agent:{sender_id}:allowed_targets"

        # Check for wildcard permission
        has_wildcard = await self.redis.sismember(allowed_key, "*")
        if has_wildcard:
            return True

        # Check specific target permission
        is_allowed = await self.redis.sismember(allowed_key, target_id)
        return bool(is_allowed)

    async def set_permissions(
        self,
        agent_id: str,
        allowed_targets: Optional[list[str]] = None,
        blocked_targets: Optional[list[str]] = None,
    ) -> None:
        """
        Set ACL permissions for an agent.

        Args:
            agent_id: Agent ID to set permissions for
            allowed_targets: List of allowed target IDs (None = no change)
            blocked_targets: List of blocked target IDs (None = no change)
        """
        if allowed_targets is not None:
            allowed_key = f"agent:{agent_id}:allowed_targets"
            # Clear existing permissions
            await self.redis.delete(allowed_key)
            # Add new permissions
            if allowed_targets:
                await self.redis.sadd(allowed_key, *allowed_targets)
            logger.info(
                "Updated allowed targets",
                extra={"agent_id": agent_id, "count": len(allowed_targets)},
            )

        if blocked_targets is not None:
            blocked_key = f"agent:{agent_id}:blocked_targets"
            # Clear existing blocks
            await self.redis.delete(blocked_key)
            # Add new blocks
            if blocked_targets:
                await self.redis.sadd(blocked_key, *blocked_targets)
            logger.info(
                "Updated blocked targets",
                extra={"agent_id": agent_id, "count": len(blocked_targets)},
            )

    async def add_permission(self, agent_id: str, target_id: str) -> None:
        """
        Add permission for agent to message target.

        Args:
            agent_id: Agent ID
            target_id: Target ID to allow
        """
        allowed_key = f"agent:{agent_id}:allowed_targets"
        await self.redis.sadd(allowed_key, target_id)
        logger.info(
            "Added permission", extra={"agent_id": agent_id, "target": target_id}
        )

    async def remove_permission(self, agent_id: str, target_id: str) -> None:
        """
        Remove permission for agent to message target.

        Args:
            agent_id: Agent ID
            target_id: Target ID to remove
        """
        allowed_key = f"agent:{agent_id}:allowed_targets"
        await self.redis.srem(allowed_key, target_id)
        logger.info(
            "Removed permission", extra={"agent_id": agent_id, "target": target_id}
        )

    async def block_target(self, agent_id: str, target_id: str) -> None:
        """
        Block agent from messaging target.

        Args:
            agent_id: Agent ID
            target_id: Target ID to block
        """
        blocked_key = f"agent:{agent_id}:blocked_targets"
        await self.redis.sadd(blocked_key, target_id)
        logger.info("Blocked target", extra={"agent_id": agent_id, "target": target_id})

    async def unblock_target(self, agent_id: str, target_id: str) -> None:
        """
        Unblock agent from messaging target.

        Args:
            agent_id: Agent ID
            target_id: Target ID to unblock
        """
        blocked_key = f"agent:{agent_id}:blocked_targets"
        await self.redis.srem(blocked_key, target_id)
        logger.info(
            "Unblocked target", extra={"agent_id": agent_id, "target": target_id}
        )

    async def get_permissions(self, agent_id: str) -> dict[str, list[str]]:
        """
        Get agent's permissions.

        Args:
            agent_id: Agent ID

        Returns:
            Dictionary with "allowed" and "blocked" lists
        """
        allowed_key = f"agent:{agent_id}:allowed_targets"
        blocked_key = f"agent:{agent_id}:blocked_targets"

        allowed = await self.redis.smembers(allowed_key)
        blocked = await self.redis.smembers(blocked_key)

        return {
            "allowed": sorted(allowed) if allowed else [],
            "blocked": sorted(blocked) if blocked else [],
        }
