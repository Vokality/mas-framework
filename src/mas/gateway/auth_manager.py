"""Authorization Manager - High-level API for configuring authorization."""

import logging
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

if TYPE_CHECKING:
    from .gateway import GatewayService
    from .authorization import AuthorizationModule

logger = logging.getLogger(__name__)


class AllowOperation(TypedDict):
    type: Literal["allow"]
    sender: str
    targets: list[str]


class BlockOperation(TypedDict):
    type: Literal["block"]
    sender: str
    target: str


class CreateRoleOperation(TypedDict):
    type: Literal["create_role"]
    role_name: str
    description: str
    permissions: list[str]


class AssignRoleOperation(TypedDict):
    type: Literal["assign_role"]
    agent_id: str
    role_name: str


PendingOperation = (
    AllowOperation | BlockOperation | CreateRoleOperation | AssignRoleOperation
)


class AuthorizationManager:
    """
    High-level authorization configuration API with fluent interface.

    Provides excellent DX for common authorization patterns:
    - Bidirectional communication (A ↔ B)
    - Multi-agent networks (A → B, C, D)
    - Role-based permissions (admins, operators, viewers)
    - Wildcard permissions (agent can message anyone)

    Example:
        # Simple bidirectional communication
        auth = AuthorizationManager(gateway)
        await auth.allow_bidirectional("patient", "doctor")

        # Multi-agent network
        await (auth
            .allow("agent1", ["agent2", "agent3"])
            .allow("agent2", "agent3")
            .apply())

        # Role-based permissions
        await (auth
            .create_role("doctor", permissions=["send:patient*"])
            .create_role("admin", permissions=["send:*", "manage:*"])
            .assign_role("dr_smith", "doctor")
            .assign_role("super_admin", "admin")
            .apply())
    """

    def __init__(self, gateway: "GatewayService"):
        """
        Initialize authorization manager.

        Args:
            gateway: GatewayService instance with authorization module
        """
        self._gateway = gateway
        self._authz: "AuthorizationModule" = gateway._authz  # type: ignore
        if not self._authz:
            raise RuntimeError(
                "Gateway must be started before using AuthorizationManager"
            )
        self._pending_operations: list[PendingOperation] = []

    async def allow_bidirectional(self, agent_a: str, agent_b: str) -> None:
        """
        Allow bidirectional communication between two agents.

        This is the most common pattern for agent pairs that need to talk to each other.

        Args:
            agent_a: First agent ID
            agent_b: Second agent ID

        Example:
            await auth.allow_bidirectional("patient_jones", "doctor_smith")
        """
        await self._authz.add_permission(agent_a, agent_b)
        await self._authz.add_permission(agent_b, agent_a)
        logger.info(f"Bidirectional communication enabled: {agent_a} ↔ {agent_b}")

    async def allow_network(
        self, agents: list[str], bidirectional: bool = True
    ) -> None:
        """
        Allow communication between all agents in a network (full mesh).

        Args:
            agents: List of agent IDs
            bidirectional: If True, all agents can message each other (default: True)
                          If False, only allow messages in order (agents[0] → agents[1] → ...)

        Example:
            # Full mesh: everyone can message everyone
            await auth.allow_network(["agent1", "agent2", "agent3"])

            # Chain: agent1 → agent2 → agent3
            await auth.allow_network(["agent1", "agent2", "agent3"], bidirectional=False)
        """
        if bidirectional:
            # Full mesh: every agent can message every other agent
            for sender in agents:
                targets = [a for a in agents if a != sender]
                if targets:
                    await self._authz.set_permissions(sender, allowed_targets=targets)
            logger.info(f"Full mesh network enabled for {len(agents)} agents")
        else:
            # Chain: agent[i] can only message agent[i+1]
            for i in range(len(agents) - 1):
                await self._authz.add_permission(agents[i], agents[i + 1])
            logger.info(f"Chain network enabled: {' → '.join(agents)}")

    async def allow_broadcast(self, sender: str, receivers: list[str]) -> None:
        """
        Allow one agent to broadcast to multiple receivers (one-way).

        Args:
            sender: Agent ID that can broadcast
            receivers: List of agent IDs that can receive

        Example:
            # Coordinator can send to all workers, but workers can't reply
            await auth.allow_broadcast("coordinator", ["worker1", "worker2", "worker3"])
        """
        await self._authz.set_permissions(sender, allowed_targets=receivers)
        logger.info(f"Broadcast enabled: {sender} → {len(receivers)} receivers")

    async def allow_wildcard(self, agent_id: str) -> None:
        """
        Allow agent to message anyone (wildcard permission).

        Use sparingly - typically for admin/supervisor agents only.

        Args:
            agent_id: Agent ID to grant wildcard permission

        Example:
            await auth.allow_wildcard("supervisor_agent")
        """
        await self._authz.set_permissions(agent_id, allowed_targets=["*"])
        logger.info(f"Wildcard permission granted: {agent_id} can message anyone")

    def allow(self, sender: str, targets: str | list[str]) -> "AuthorizationManager":
        """
        Queue permission grant (chainable, applied with .apply()).

        Args:
            sender: Sending agent ID
            targets: Target agent ID(s)

        Returns:
            Self for chaining

        Example:
            await (auth
                .allow("agent1", "agent2")
                .allow("agent2", ["agent3", "agent4"])
                .apply())
        """
        target_list = [targets] if isinstance(targets, str) else list(targets)
        self._pending_operations.append(
            AllowOperation(type="allow", sender=sender, targets=target_list)
        )
        return self

    def block(self, sender: str, target: str) -> "AuthorizationManager":
        """
        Queue permission block (chainable, applied with .apply()).

        Args:
            sender: Sending agent ID
            target: Target agent ID to block

        Returns:
            Self for chaining

        Example:
            await (auth
                .allow("agent1", "*")  # Can message anyone
                .block("agent1", "agent2")  # Except agent2
                .apply())
        """
        self._pending_operations.append(
            BlockOperation(type="block", sender=sender, target=target)
        )
        return self

    def create_role(
        self,
        role_name: str,
        description: str = "",
        permissions: list[str] | None = None,
    ) -> "AuthorizationManager":
        """
        Queue role creation (chainable, applied with .apply()).

        Args:
            role_name: Role name (e.g., "doctor", "admin")
            description: Role description
            permissions: Permission patterns (e.g., ["send:patient*", "read:*"])

        Returns:
            Self for chaining

        Example:
            await (auth
                .create_role("doctor", permissions=["send:patient*"])
                .create_role("admin", permissions=["send:*", "manage:*"])
                .apply())
        """
        if permissions is None:
            permission_list: list[str] = []
        else:
            permission_list = list(permissions)
        self._pending_operations.append(
            CreateRoleOperation(
                type="create_role",
                role_name=role_name,
                description=description,
                permissions=permission_list,
            )
        )
        return self

    def assign_role(self, agent_id: str, role_name: str) -> "AuthorizationManager":
        """
        Queue role assignment (chainable, applied with .apply()).

        Args:
            agent_id: Agent ID
            role_name: Role name to assign

        Returns:
            Self for chaining

        Example:
            await (auth
                .create_role("doctor", permissions=["send:*"])
                .assign_role("dr_smith", "doctor")
                .assign_role("dr_jones", "doctor")
                .apply())
        """
        self._pending_operations.append(
            AssignRoleOperation(
                type="assign_role",
                agent_id=agent_id,
                role_name=role_name,
            )
        )
        return self

    async def apply(self) -> None:
        """
        Apply all queued operations.

        Example:
            await (auth
                .allow("agent1", "agent2")
                .allow("agent2", "agent3")
                .apply())
        """
        for op in self._pending_operations:
            op_type = op["type"]

            if op_type == "allow":
                allow_op = cast(AllowOperation, op)
                for target in allow_op["targets"]:
                    await self._authz.add_permission(allow_op["sender"], target)

            elif op_type == "block":
                block_op = cast(BlockOperation, op)
                await self._authz.block_target(block_op["sender"], block_op["target"])

            elif op_type == "create_role":
                role_op = cast(CreateRoleOperation, op)
                await self._authz.create_role(
                    role_op["role_name"],
                    role_op["description"],
                    role_op["permissions"],
                )

            elif op_type == "assign_role":
                assign_op = cast(AssignRoleOperation, op)
                await self._authz.assign_role(
                    assign_op["agent_id"], assign_op["role_name"]
                )

        logger.info(f"Applied {len(self._pending_operations)} authorization operations")
        self._pending_operations.clear()

    async def get_summary(self, agent_id: str) -> dict[str, Any]:
        """
        Get human-readable authorization summary for an agent.

        Args:
            agent_id: Agent ID

        Returns:
            Dictionary with ACL permissions, roles, and computed permissions

        Example:
            summary = await auth.get_summary("doctor_smith")
            print(summary)  # {"acl": {"allowed": [...], "blocked": [...]}, "roles": [...]}
        """
        acl = await self._authz.get_permissions(agent_id)
        roles = await self._authz.get_agent_roles(agent_id)

        role_permissions: dict[str, list[str]] = {}
        for role in roles:
            role_permissions[role] = await self._authz.get_role_permissions(role)

        return {
            "agent_id": agent_id,
            "acl": acl,
            "roles": roles,
            "role_permissions": role_permissions,
        }
