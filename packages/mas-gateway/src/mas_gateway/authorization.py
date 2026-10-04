"""Authorization Module for Gateway Service."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import NamedTuple

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
_STRINGS_ADAPTER = TypeAdapter(set[str])
_HASH_ADAPTER = TypeAdapter(dict[str, str])
_SCAN_ADAPTER = TypeAdapter(tuple[int, list[str]])


class _RoleSnapshot(NamedTuple):
    roles: tuple[str, ...]
    permissions: frozenset[str]
    unchanged: bool


class _AccessSnapshot(NamedTuple):
    accessible: bool
    acl_allowed: bool
    roles: tuple[str, ...]
    permissions: frozenset[str]
    unchanged: bool


_ROLE_SNAPSHOT_ADAPTER = TypeAdapter(_RoleSnapshot)
_ACCESS_SNAPSHOT_ADAPTER = TypeAdapter(_AccessSnapshot)
_READ_ROLES = """
local function role_permissions(role_index, argument_start)
    local roles = redis.call('SMEMBERS', KEYS[role_index])
    if #roles == 0 then return {roles, {}, 1} end
    if #roles ~= #KEYS - role_index then return {roles, {}, 0} end
    local expected = {}
    for index = argument_start, #ARGV do expected[ARGV[index]] = true end
    for _, role in ipairs(roles) do
        if not expected[role] then return {roles, {}, 0} end
    end
    local permissions = {}
    for index = role_index + 1, #KEYS do
        for _, permission in ipairs(redis.call('SMEMBERS', KEYS[index])) do
            permissions[#permissions + 1] = permission
        end
    end
    return {roles, permissions, 1}
end
"""
_ACCESS_SCRIPT = (
    _READ_ROLES
    + """
local now = redis.call('TIME')
local current = now[1] * 1000 + math.floor(now[2] / 1000)
if redis.call('ZCOUNT', KEYS[1], '(' .. current, '+inf') == 0 then
    return {0, 0, {}, {}, 1}
end
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return {0, 0, {}, {}, 1}
end
if redis.call('SISMEMBER', KEYS[3], '*') == 1
    or redis.call('SISMEMBER', KEYS[3], ARGV[1]) == 1 then
    return {1, 1, {}, {}, 1}
end
if ARGV[2] == '0' then return {1, 0, {}, {}, 1} end
local roles = role_permissions(4, 3)
return {1, 0, roles[1], roles[2], roles[3]}
"""
)
_ROLE_SCRIPT = _READ_ROLES + "return role_permissions(1, 1)"


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

    def __init__(self, redis: Redis, enable_rbac: bool) -> None:
        """
        Initialize authorization module.

        Args:
            redis: Redis connection
            enable_rbac: Enable RBAC authorization
        """
        self.redis: Redis = redis
        self.enable_rbac = enable_rbac
        self._role_hints: OrderedDict[str, tuple[str, ...]] = OrderedDict()

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
        snapshot = await self._access_snapshot(sender_id, target_id, self.enable_rbac)
        if not snapshot.accessible:
            return False
        allowed = snapshot.acl_allowed or any(
            self._matches_permission(f"{action}:{target_id}", permission)
            for permission in snapshot.permissions
        )

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
        snapshot = await self._access_snapshot(sender_id, target_id, False)
        return snapshot.accessible and snapshot.acl_allowed

    async def _access_snapshot(
        self, sender_id: str, target_id: str, include_rbac: bool
    ) -> _AccessSnapshot:
        """Read grants atomically, supplying every accessed role key explicitly."""
        roles = self._role_hints.get(sender_id, ())
        for _ in range(4):
            keys = (
                f"mas.sessions:{target_id}",
                f"agent:{sender_id}:blocked_targets",
                f"agent:{sender_id}:allowed_targets",
                f"agent:{sender_id}:roles",
                *(f"role:{role}:permissions" for role in roles),
            )
            snapshot = _ACCESS_SNAPSHOT_ADAPTER.validate_python(
                await self.redis.eval_ro(
                    _ACCESS_SCRIPT,
                    len(keys),
                    *keys,
                    target_id,
                    int(include_rbac),
                    *roles,
                )
            )
            self._remember_role_hint(sender_id, snapshot.roles)
            if snapshot.unchanged:
                return snapshot
            roles = snapshot.roles
        raise RedisError("authorization_changed_during_read")

    def _remember_role_hint(self, agent_id: str, roles: tuple[str, ...]) -> None:
        """Bound non-authoritative discovery hints; never cache grants or decisions."""
        if len(roles) > 64 or len(agent_id) + sum(map(len, roles)) > 4096:
            self._role_hints.pop(agent_id, None)
            return
        self._role_hints[agent_id] = roles
        self._role_hints.move_to_end(agent_id)
        if len(self._role_hints) > 2048:
            self._role_hints.popitem(last=False)

    async def set_permissions(
        self,
        agent_id: str,
        allowed_targets: list[str] | None = None,
        blocked_targets: list[str] | None = None,
    ) -> None:
        """
        Set ACL permissions for an agent.

        Args:
            agent_id: Agent ID to set permissions for
            allowed_targets: List of allowed target IDs (None = no change)
            blocked_targets: List of blocked target IDs (None = no change)
        """
        async with self.redis.pipeline() as pipe:
            for kind, targets in (
                ("allowed", allowed_targets),
                ("blocked", blocked_targets),
            ):
                if targets is None:
                    continue
                key = f"agent:{agent_id}:{kind}_targets"
                pipe.delete(key)
                if targets:
                    pipe.sadd(key, *targets)
            await pipe.execute()

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

        allowed = _STRINGS_ADAPTER.validate_python(
            await self.redis.smembers(allowed_key)
        )
        blocked = _STRINGS_ADAPTER.validate_python(
            await self.redis.smembers(blocked_key)
        )

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
        roles = self._role_hints.get(agent_id, ())
        for _ in range(4):
            keys = (
                f"agent:{agent_id}:roles",
                *(f"role:{role}:permissions" for role in roles),
            )
            snapshot = _ROLE_SNAPSHOT_ADAPTER.validate_python(
                await self.redis.eval_ro(_ROLE_SCRIPT, len(keys), *keys, *roles)
            )
            self._remember_role_hint(agent_id, snapshot.roles)
            if snapshot.unchanged:
                return any(
                    self._matches_permission(permission, granted)
                    for granted in snapshot.permissions
                )
            roles = snapshot.roles
        raise RedisError("authorization_changed_during_read")

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
        permissions: list[str] | None = None,
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
        permissions = _STRINGS_ADAPTER.validate_python(
            await self.redis.smembers(perms_key)
        )
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
        roles = _STRINGS_ADAPTER.validate_python(await self.redis.smembers(roles_key))
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
            cursor, keys = _SCAN_ADAPTER.validate_python(
                await self.redis.scan(cursor, match="role:*", count=100)
            )
            # Filter out permission keys
            role_keys.extend([k for k in keys if not k.endswith(":permissions")])
            if cursor == 0:
                break

        roles: list[dict[str, str]] = []
        for role_key in role_keys:
            role_data = _HASH_ADAPTER.validate_python(
                await self.redis.hgetall(role_key)
            )
            if role_data:
                roles.append(role_data)

        return roles
