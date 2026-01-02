"""Tests for RBAC (Role-Based Access Control) functionality."""

import pytest
from mas.gateway.authorization import AuthorizationModule

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def authz_rbac(redis):
    """Create AuthorizationModule with RBAC enabled."""
    return AuthorizationModule(redis, enable_rbac=True)


@pytest.fixture
async def authz_acl_only(redis):
    """Create AuthorizationModule with ACL only (no RBAC)."""
    return AuthorizationModule(redis, enable_rbac=False)


class TestRoleCreation:
    """Test role creation and management."""

    async def test_create_role(self, authz_rbac):
        """Test creating a basic role."""
        await authz_rbac.create_role(
            "admin",
            description="Administrator role",
            permissions=["send:*", "read:*", "manage:*"],
        )

        # Verify role exists
        perms = await authz_rbac.get_role_permissions("admin")
        assert set(perms) == {"send:*", "read:*", "manage:*"}

    async def test_create_role_no_permissions(self, authz_rbac):
        """Test creating a role without permissions."""
        await authz_rbac.create_role("empty", description="Empty role")

        perms = await authz_rbac.get_role_permissions("empty")
        assert perms == []

    async def test_delete_role(self, authz_rbac):
        """Test deleting a role."""
        await authz_rbac.create_role("temporary", permissions=["read:*"])
        await authz_rbac.delete_role("temporary")

        perms = await authz_rbac.get_role_permissions("temporary")
        assert perms == []


class TestRolePermissions:
    """Test role permission management."""

    async def test_add_role_permission(self, authz_rbac):
        """Test adding permissions to a role."""
        await authz_rbac.create_role("operator")
        await authz_rbac.add_role_permission("operator", "send:agent.*")
        await authz_rbac.add_role_permission("operator", "read:agent.*")

        perms = await authz_rbac.get_role_permissions("operator")
        assert set(perms) == {"send:agent.*", "read:agent.*"}

    async def test_remove_role_permission(self, authz_rbac):
        """Test removing permissions from a role."""
        await authz_rbac.create_role("operator", permissions=["send:*", "read:*"])
        await authz_rbac.remove_role_permission("operator", "read:*")

        perms = await authz_rbac.get_role_permissions("operator")
        assert perms == ["send:*"]


class TestAgentRoleAssignment:
    """Test assigning roles to agents."""

    async def test_assign_role(self, authz_rbac, redis):
        """Test assigning a role to an agent."""
        # Create agent
        await redis.hset("agent:agent-1", mapping={"status": "ACTIVE"})

        # Create role and assign
        await authz_rbac.create_role("admin", permissions=["send:*"])
        await authz_rbac.assign_role("agent-1", "admin")

        roles = await authz_rbac.get_agent_roles("agent-1")
        assert roles == ["admin"]

    async def test_assign_multiple_roles(self, authz_rbac, redis):
        """Test assigning multiple roles to an agent."""
        await redis.hset("agent:agent-1", mapping={"status": "ACTIVE"})

        await authz_rbac.create_role("operator")
        await authz_rbac.create_role("auditor")
        await authz_rbac.assign_role("agent-1", "operator")
        await authz_rbac.assign_role("agent-1", "auditor")

        roles = await authz_rbac.get_agent_roles("agent-1")
        assert set(roles) == {"operator", "auditor"}

    async def test_unassign_role(self, authz_rbac, redis):
        """Test removing a role from an agent."""
        await redis.hset("agent:agent-1", mapping={"status": "ACTIVE"})

        await authz_rbac.create_role("admin")
        await authz_rbac.assign_role("agent-1", "admin")
        await authz_rbac.unassign_role("agent-1", "admin")

        roles = await authz_rbac.get_agent_roles("agent-1")
        assert roles == []


class TestPermissionMatching:
    """Test permission pattern matching."""

    async def test_exact_match(self, authz_rbac):
        """Test exact permission match."""
        assert authz_rbac._matches_permission("send:agent-1", "send:agent-1")

    async def test_wildcard_all(self, authz_rbac):
        """Test * wildcard matches everything."""
        assert authz_rbac._matches_permission("send:agent-1", "*")
        assert authz_rbac._matches_permission("read:anything", "*")

    async def test_wildcard_action(self, authz_rbac):
        """Test action:* wildcard."""
        assert authz_rbac._matches_permission("send:agent-1", "send:*")
        assert authz_rbac._matches_permission("send:agent-2", "send:*")
        assert not authz_rbac._matches_permission("read:agent-1", "send:*")

    async def test_wildcard_pattern(self, authz_rbac):
        """Test pattern wildcards like agent.*"""
        # agent.* means "agent." followed by anything
        assert authz_rbac._matches_permission("send:agent.1", "send:agent.*")
        assert authz_rbac._matches_permission("send:agent.123", "send:agent.*")
        assert authz_rbac._matches_permission("send:agent.foo", "send:agent.*")
        assert not authz_rbac._matches_permission("send:user.1", "send:agent.*")
        assert not authz_rbac._matches_permission("send:agent-1", "send:agent.*")

    async def test_no_match(self, authz_rbac):
        """Test permission doesn't match."""
        assert not authz_rbac._matches_permission("send:agent-1", "read:agent-1")
        assert not authz_rbac._matches_permission("send:agent-1", "send:agent-2")


class TestRBACAuthorization:
    """Test RBAC authorization checks."""

    async def test_rbac_allows_with_wildcard(self, authz_rbac, redis):
        """Test RBAC authorization with wildcard permission."""
        # Setup agents
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # Create role with wildcard permission
        await authz_rbac.create_role("admin", permissions=["send:*"])
        await authz_rbac.assign_role("sender", "admin")

        # Check authorization
        allowed = await authz_rbac.check_rbac("sender", "send:target")
        assert allowed is True

    async def test_rbac_allows_with_specific_permission(self, authz_rbac, redis):
        """Test RBAC authorization with specific permission."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target-1", mapping={"status": "ACTIVE"})

        await authz_rbac.create_role("limited", permissions=["send:target-1"])
        await authz_rbac.assign_role("sender", "limited")

        allowed = await authz_rbac.check_rbac("sender", "send:target-1")
        assert allowed is True

    async def test_rbac_denies_without_permission(self, authz_rbac, redis):
        """Test RBAC denies when permission not granted."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        await authz_rbac.create_role("readonly", permissions=["read:*"])
        await authz_rbac.assign_role("sender", "readonly")

        allowed = await authz_rbac.check_rbac("sender", "send:target")
        assert allowed is False

    async def test_rbac_denies_without_roles(self, authz_rbac):
        """Test RBAC denies when agent has no roles."""
        allowed = await authz_rbac.check_rbac("sender", "send:target")
        assert allowed is False

    async def test_rbac_with_pattern_permission(self, authz_rbac, redis):
        """Test RBAC with pattern-based permissions."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})

        await authz_rbac.create_role("operator", permissions=["send:agent.*"])
        await authz_rbac.assign_role("sender", "operator")

        # Should allow matching pattern (agent.* means agent. followed by anything)
        assert await authz_rbac.check_rbac("sender", "send:agent.1") is True
        assert await authz_rbac.check_rbac("sender", "send:agent.123") is True
        assert await authz_rbac.check_rbac("sender", "send:agent.foo") is True

        # Should deny non-matching pattern
        assert await authz_rbac.check_rbac("sender", "send:user.1") is False
        assert await authz_rbac.check_rbac("sender", "send:agent-1") is False


class TestCombinedACLAndRBAC:
    """Test combined ACL and RBAC authorization."""

    async def test_rbac_allows_when_acl_denies(self, authz_rbac, redis):
        """Test that RBAC can grant access even if ACL doesn't."""
        # Setup agents
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # No ACL permissions set, but RBAC grants access
        await authz_rbac.create_role("sender_role", permissions=["send:*"])
        await authz_rbac.assign_role("sender", "sender_role")

        # Should be authorized via RBAC
        allowed = await authz_rbac.authorize("sender", "target", "send")
        assert allowed is True

    async def test_acl_allows_when_rbac_denies(self, authz_rbac, redis):
        """Test that ACL can grant access even if RBAC doesn't."""
        # Setup agents
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # Set ACL permission
        await authz_rbac.set_permissions("sender", allowed_targets=["target"])

        # No RBAC role assigned
        # Should be authorized via ACL
        allowed = await authz_rbac.authorize("sender", "target", "send")
        assert allowed is True

    async def test_both_acl_and_rbac_allow(self, authz_rbac, redis):
        """Test when both ACL and RBAC grant permission."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # Set both ACL and RBAC permissions
        await authz_rbac.set_permissions("sender", allowed_targets=["target"])
        await authz_rbac.create_role("admin", permissions=["send:*"])
        await authz_rbac.assign_role("sender", "admin")

        allowed = await authz_rbac.authorize("sender", "target", "send")
        assert allowed is True

    async def test_neither_acl_nor_rbac_allow(self, authz_rbac, redis):
        """Test when neither ACL nor RBAC grant permission."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # No permissions set
        allowed = await authz_rbac.authorize("sender", "target", "send")
        assert allowed is False


class TestBackwardCompatibility:
    """Test backward compatibility with ACL-only mode."""

    async def test_acl_only_ignores_rbac(self, authz_acl_only, redis):
        """Test that ACL-only mode doesn't check RBAC."""
        await redis.hset("agent:sender", mapping={"status": "ACTIVE"})
        await redis.hset("agent:target", mapping={"status": "ACTIVE"})

        # Create RBAC role (should be ignored)
        await authz_acl_only.create_role("admin", permissions=["send:*"])
        await authz_acl_only.assign_role("sender", "admin")

        # Should be denied because ACL permission not set
        allowed = await authz_acl_only.authorize("sender", "target", "send")
        assert allowed is False

        # Add ACL permission
        await authz_acl_only.set_permissions("sender", allowed_targets=["target"])

        # Now should be allowed via ACL
        allowed = await authz_acl_only.authorize("sender", "target", "send")
        assert allowed is True


class TestRoleList:
    """Test listing roles."""

    async def test_list_roles(self, authz_rbac):
        """Test listing all roles."""
        await authz_rbac.create_role("admin", description="Administrator")
        await authz_rbac.create_role("operator", description="Operator")
        await authz_rbac.create_role("readonly", description="Read-only")

        roles = await authz_rbac.list_roles()

        # Check we got all roles
        role_names = {r["name"] for r in roles}
        assert role_names == {"admin", "operator", "readonly"}

    async def test_list_roles_empty(self, authz_rbac):
        """Test listing roles when none exist."""
        roles = await authz_rbac.list_roles()
        assert roles == []
