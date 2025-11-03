"""Tests for AuthorizationManager high-level API."""

import pytest
from redis.asyncio import Redis
from mas.gateway import GatewayService, AuthorizationManager
from mas.gateway.config import GatewaySettings, FeaturesSettings

pytestmark = pytest.mark.asyncio


def _test_settings():
    """Create test gateway settings with simplified config."""
    return GatewaySettings(
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=False,
        )
    )


@pytest.fixture
async def redis():
    """Redis connection fixture."""
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)
    yield r
    await r.flushdb()
    await r.aclose()


async def test_authorization_manager_import():
    """Test that AuthorizationManager can be imported."""
    assert AuthorizationManager is not None


async def test_auth_manager_from_gateway(redis):
    """Test getting AuthorizationManager from gateway."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        assert isinstance(auth, AuthorizationManager)
    finally:
        await gateway.stop()


async def test_allow_bidirectional(redis):
    """Test bidirectional permission."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_bidirectional("agent1", "agent2")

        # Check both directions are allowed
        authz = gateway._authz
        assert authz is not None
        perms1 = await authz.get_permissions("agent1")
        perms2 = await authz.get_permissions("agent2")

        assert "agent2" in perms1["allowed"]
        assert "agent1" in perms2["allowed"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_allow_network_full_mesh(redis):
    """Test full mesh network."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_network(["agent1", "agent2", "agent3"])

        # Check all agents can message each other
        authz = gateway._authz
        assert authz is not None
        perms1 = await authz.get_permissions("agent1")
        perms2 = await authz.get_permissions("agent2")
        perms3 = await authz.get_permissions("agent3")

        assert set(perms1["allowed"]) == {"agent2", "agent3"}
        assert set(perms2["allowed"]) == {"agent1", "agent3"}
        assert set(perms3["allowed"]) == {"agent1", "agent2"}
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_allow_network_chain(redis):
    """Test chain network (unidirectional)."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_network(["agent1", "agent2", "agent3"], bidirectional=False)

        # Check chain: agent1 → agent2 → agent3
        authz = gateway._authz
        assert authz is not None
        perms1 = await authz.get_permissions("agent1")
        perms2 = await authz.get_permissions("agent2")
        perms3 = await authz.get_permissions("agent3")

        assert "agent2" in perms1["allowed"]
        assert "agent1" not in perms1["allowed"]

        assert "agent3" in perms2["allowed"]
        assert "agent1" not in perms2["allowed"]

        assert len(perms3["allowed"]) == 0  # End of chain
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_allow_broadcast(redis):
    """Test broadcast pattern (one-to-many)."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_broadcast("coordinator", ["worker1", "worker2", "worker3"])

        # Check coordinator can message all workers
        authz = gateway._authz
        assert authz is not None
        perms = await authz.get_permissions("coordinator")

        assert set(perms["allowed"]) == {"worker1", "worker2", "worker3"}

        # Check workers can't message coordinator (one-way)
        worker1_perms = await authz.get_permissions("worker1")
        assert "coordinator" not in worker1_perms["allowed"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_allow_wildcard(redis):
    """Test wildcard permission."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_wildcard("admin")

        # Check admin has wildcard
        authz = gateway._authz
        assert authz is not None
        perms = await authz.get_permissions("admin")

        assert "*" in perms["allowed"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_chainable_api(redis):
    """Test chainable fluent API."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await (
            auth.allow("agent1", "agent2")
            .allow("agent2", ["agent3", "agent4"])
            .allow("agent3", "agent4")
            .apply()
        )

        # Check all permissions were applied
        authz = gateway._authz
        assert authz is not None
        perms1 = await authz.get_permissions("agent1")
        perms2 = await authz.get_permissions("agent2")
        perms3 = await authz.get_permissions("agent3")

        assert "agent2" in perms1["allowed"]
        assert set(perms2["allowed"]) == {"agent3", "agent4"}
        assert "agent4" in perms3["allowed"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_block_target(redis):
    """Test blocking specific target."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await (
            auth.allow("agent1", "*")  # Wildcard
            .block("agent1", "agent2")  # Block agent2
            .apply()
        )

        # Check agent1 has wildcard but blocks agent2
        authz = gateway._authz
        assert authz is not None
        perms = await authz.get_permissions("agent1")

        assert "*" in perms["allowed"]
        assert "agent2" in perms["blocked"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_create_role_and_assign(redis):
    """Test role creation and assignment."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await (
            auth.create_role("doctor", "Medical professional", ["send:patient*"])
            .create_role("admin", "Administrator", ["send:*", "manage:*"])
            .assign_role("dr_smith", "doctor")
            .assign_role("super_admin", "admin")
            .apply()
        )

        # Check roles exist
        authz = gateway._authz
        assert authz is not None
        doctor_perms = await authz.get_role_permissions("doctor")
        admin_perms = await authz.get_role_permissions("admin")

        assert "send:patient*" in doctor_perms
        assert "send:*" in admin_perms
        assert "manage:*" in admin_perms

        # Check role assignments
        dr_smith_roles = await authz.get_agent_roles("dr_smith")
        super_admin_roles = await authz.get_agent_roles("super_admin")

        assert "doctor" in dr_smith_roles
        assert "admin" in super_admin_roles
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_get_summary(redis):
    """Test authorization summary."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        auth = gateway.auth_manager()
        await auth.allow_bidirectional("agent1", "agent2")
        await (
            auth.create_role("operator", "Operator role", ["send:*"])
            .assign_role("agent1", "operator")
            .apply()
        )

        # Get summary
        summary = await auth.get_summary("agent1")

        assert summary["agent_id"] == "agent1"
        assert "agent2" in summary["acl"]["allowed"]
        assert "operator" in summary["roles"]
        assert "send:*" in summary["role_permissions"]["operator"]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_realistic_healthcare_scenario(redis):
    """Test realistic healthcare authorization scenario."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        # Register agents first (required for check_acl validation)
        await redis.hset("agent:patient_jones", mapping={"status": "ACTIVE"})
        await redis.hset("agent:doctor_smith", mapping={"status": "ACTIVE"})

        # This is what users actually need - simple and clear
        auth = gateway.auth_manager()
        await auth.allow_bidirectional("patient_jones", "doctor_smith")

        # Verify it works
        authz = gateway._authz
        assert authz is not None
        assert await authz.check_acl("patient_jones", "doctor_smith")
        assert await authz.check_acl("doctor_smith", "patient_jones")
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_realistic_team_scenario(redis):
    """Test realistic team collaboration scenario."""
    gateway = GatewayService(settings=_test_settings())
    await gateway.start()

    try:
        # Register agents first (required for check_acl validation)
        for agent_id in ["coordinator", "worker1", "worker2", "worker3", "supervisor"]:
            await redis.hset(f"agent:{agent_id}", mapping={"status": "ACTIVE"})

        # Team of 5 agents that all need to talk
        auth = gateway.auth_manager()
        await auth.allow_network(
            ["coordinator", "worker1", "worker2", "worker3", "supervisor"]
        )

        # Verify full mesh works
        authz = gateway._authz
        assert authz is not None
        assert await authz.check_acl("coordinator", "worker1")
        assert await authz.check_acl("worker1", "coordinator")
        assert await authz.check_acl("worker2", "worker3")
        assert await authz.check_acl("supervisor", "coordinator")
    finally:
        await gateway.stop()
