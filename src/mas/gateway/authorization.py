"""Authorization Module for Gateway Service."""

from __future__ import annotations

import logging
import re
from typing import Optional
from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


class AuthorizationModule:
    """
    Authorization module for enforcing access control.

    Implements Phase 1 ACL and Phase 2 RBAC as per GATEWAY.md:

    Phase 1 - ACL (Access Control List):
    - Simple allow-list per agent
    - Wildcard support ("*" allows all)
    - Block-list takes precedence over allow-list
    - Default deny (explicit allow required)

    Phase 2 - RBAC (Role-Based Access Control):
    - Role definitions with permission sets
    - Agent role assignments
    - Permission patterns (e.g., "send:*", "read:agent.*")
    - Hierarchical permission checking

    Redis Data Model:
        # ACL (Phase 1)
        agent:{agent_id}:allowed_targets → Set of allowed target IDs
        agent:{agent_id}:blocked_targets → Set of blocked target IDs

        # RBAC (Phase 2)
        role:{role_name} → Hash with metadata
        role:{role_name}:permissions → Set of permission patterns
        agent:{agent_id}:roles → Set of role names
    """

    def __init__(self, redis: AsyncRedisProtocol, enable_rbac: bool = False):
        """
        Initialize authorization module.

        Args:
            redis: Redis connection
            enable_rbac: Enable RBAC authorization (default: False, ACL only)
        """
        self.redis: AsyncRedisProtocol = redis
        self.enable_rbac = enable_rbac

    async def authorize(
        self, sender_id: str, target_id: str, action: str = "send"
    ) -> bool:
        """
        Authorize message from sender to target.

        Args:
            sender_id: Sending agent ID
            target_id: Target agent ID
            action: Action type (e.g., "send", "read", "manage")

        Returns:
            True if authorized, False otherwise
        """
        # Check ACL first (backward compatibility)
        acl_allowed = await self.check_acl(sender_id, target_id)

        # If RBAC is enabled, also check RBAC
        if self.enable_rbac:
            # Construct permission string based on action and target
            permission = f"{action}:{target_id}"
            rbac_allowed = await self.check_rbac(sender_id, permission)

            # Allow if either ACL or RBAC grants permission
            allowed = acl_allowed or rbac_allowed
        else:
            allowed = acl_allowed

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
        await self._set_blocked(agent_id=agent_id, target_id=target_id, blocked=True)

    async def unblock_target(self, agent_id: str, target_id: str) -> None:
        """
        Unblock agent from messaging target.

        Args:
            agent_id: Agent ID
            target_id: Target ID to unblock
        """
        await self._set_blocked(agent_id=agent_id, target_id=target_id, blocked=False)

    async def _set_blocked(
        self, *, agent_id: str, target_id: str, blocked: bool
    ) -> None:
        """Update blocked targets for an agent."""
        blocked_key = f"agent:{agent_id}:blocked_targets"
        if blocked:
            await self.redis.sadd(blocked_key, target_id)
            logger.info(
                "Blocked target",
                extra={"agent_id": agent_id, "target": target_id},
            )
        else:
            await self.redis.srem(blocked_key, target_id)
            logger.info(
                "Unblocked target",
                extra={"agent_id": agent_id, "target": target_id},
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

    # ========== RBAC Methods (Phase 2) ==========

    async def check_rbac(self, agent_id: str, permission: str) -> bool:
        """
        Check if agent has permission via RBAC roles.

        Args:
            agent_id: Agent ID
            permission: Permission string (e.g., "send:agent-123", "read:*")

        Returns:
            True if agent has permission through any of their roles
        """
        # Get agent's roles
        roles_key = f"agent:{agent_id}:roles"
        roles = await self.redis.smembers(roles_key)

        if not roles:
            return False

        # Check each role's permissions
        for role in roles:
            role_perms_key = f"role:{role}:permissions"
            role_permissions = await self.redis.smembers(role_perms_key)

            for role_perm in role_permissions:
                if self._matches_permission(permission, role_perm):
                    logger.debug(
                        "RBAC permission matched",
                        extra={
                            "agent_id": agent_id,
                            "role": role,
                            "required": permission,
                            "granted": role_perm,
                        },
                    )
                    return True

        return False

    def _matches_permission(self, required: str, granted: str) -> bool:
        """
        Check if required permission matches granted permission pattern.

        Supports wildcard patterns:
        - "send:*" matches any send permission
        - "send:agent.*" matches send to any agent starting with "agent."
        - "*" matches everything

        Args:
            required: Required permission (e.g., "send:agent-123")
            granted: Granted permission pattern (e.g., "send:*")

        Returns:
            True if required permission matches granted pattern
        """
        if granted == "*":
            return True

        if granted == required:
            return True

        # Convert glob-style pattern to regex
        # Escape special regex chars except *
        pattern = re.escape(granted).replace(r"\*", ".*")
        pattern = f"^{pattern}$"

        try:
            return bool(re.match(pattern, required))
        except re.error:
            logger.warning(
                "Invalid permission pattern",
                extra={"pattern": granted},
            )
            return False

    async def create_role(
        self,
        role_name: str,
        description: str = "",
        permissions: Optional[list[str]] = None,
    ) -> None:
        """
        Create a new role with permissions.

        Args:
            role_name: Name of the role (e.g., "admin", "operator")
            description: Role description
            permissions: List of permission patterns
        """
        role_key = f"role:{role_name}"

        # Store role metadata
        await self.redis.hset(
            role_key,
            mapping={
                "name": role_name,
                "description": description or "",
            },
        )

        # Store permissions
        if permissions:
            perms_key = f"role:{role_name}:permissions"
            await self.redis.sadd(perms_key, *permissions)

        logger.info(
            "Created role",
            extra={"role": role_name, "permissions": len(permissions or [])},
        )

    async def delete_role(self, role_name: str) -> None:
        """
        Delete a role.

        Args:
            role_name: Name of the role to delete
        """
        role_key = f"role:{role_name}"
        perms_key = f"role:{role_name}:permissions"

        await self.redis.delete(role_key, perms_key)

        logger.info("Deleted role", extra={"role": role_name})

    async def add_role_permission(self, role_name: str, permission: str) -> None:
        """
        Add a permission to a role.

        Args:
            role_name: Role name
            permission: Permission pattern to add
        """
        perms_key = f"role:{role_name}:permissions"
        await self.redis.sadd(perms_key, permission)

        logger.info(
            "Added role permission",
            extra={"role": role_name, "permission": permission},
        )

    async def remove_role_permission(self, role_name: str, permission: str) -> None:
        """
        Remove a permission from a role.

        Args:
            role_name: Role name
            permission: Permission pattern to remove
        """
        perms_key = f"role:{role_name}:permissions"
        await self.redis.srem(perms_key, permission)

        logger.info(
            "Removed role permission",
            extra={"role": role_name, "permission": permission},
        )

    async def get_role_permissions(self, role_name: str) -> list[str]:
        """
        Get all permissions for a role.

        Args:
            role_name: Role name

        Returns:
            List of permission patterns
        """
        perms_key = f"role:{role_name}:permissions"
        permissions = await self.redis.smembers(perms_key)
        return sorted(permissions) if permissions else []

    async def assign_role(self, agent_id: str, role_name: str) -> None:
        """
        Assign a role to an agent.

        Args:
            agent_id: Agent ID
            role_name: Role name to assign
        """
        roles_key = f"agent:{agent_id}:roles"
        await self.redis.sadd(roles_key, role_name)

        logger.info(
            "Assigned role to agent",
            extra={"agent_id": agent_id, "role": role_name},
        )

    async def unassign_role(self, agent_id: str, role_name: str) -> None:
        """
        Remove a role from an agent.

        Args:
            agent_id: Agent ID
            role_name: Role name to remove
        """
        roles_key = f"agent:{agent_id}:roles"
        await self.redis.srem(roles_key, role_name)

        logger.info(
            "Unassigned role from agent",
            extra={"agent_id": agent_id, "role": role_name},
        )

    async def get_agent_roles(self, agent_id: str) -> list[str]:
        """
        Get all roles assigned to an agent.

        Args:
            agent_id: Agent ID

        Returns:
            List of role names
        """
        roles_key = f"agent:{agent_id}:roles"
        roles = await self.redis.smembers(roles_key)
        return sorted(roles) if roles else []

    async def list_roles(self) -> list[dict[str, str]]:
        """
        List all defined roles.

        Returns:
            List of role dictionaries with name and description
        """
        # Find all role keys
        role_keys: list[str] = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="role:*", count=100)
            # Filter out permission keys
            role_keys.extend([k for k in keys if not k.endswith(":permissions")])
            if cursor == 0:
                break

        roles: list[dict[str, str]] = []
        for role_key in role_keys:
            role_data = await self.redis.hgetall(role_key)
            if role_data:
                roles.append(role_data)

        return roles
