from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from mas.persistence.base import BasePersistenceProvider
from mas.protocol import AgentStatus, Message, MessageType
from mas.transport.service import TransportService


@dataclass
class AgentRuntime:
    """Runtime context for an agent that encapsulates infrastructure services."""

    agent_id: str
    transport: TransportService
    persistence: BasePersistenceProvider
    core_id: str = field(default="core")
    capabilities: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    async def send_message(
        self,
        content: Dict[str, Any],
        target_id: str,
        message_type: MessageType = MessageType.AGENT_MESSAGE,
    ) -> None:
        """
        Send a message to another agent.
        """

        message = Message(
            payload=content,
            sender_id=self.agent_id,
            target_id=target_id,
            message_type=message_type,
        )
        await self.transport.send_message(message)

    async def register(self) -> None:
        """
        Register agent with transport and core service.
        Component registration is separate from discovery registration.
        """
        # Register with transport for lifecycle management
        await self.transport.register_component(self.agent_id)
        
        # Send discovery registration message to core
        await self.send_message(
            content={
                "status": AgentStatus.ACTIVE,
                "metadata": self.metadata,
                "capabilities": list(self.capabilities),
            },
            target_id=self.core_id,
            message_type=MessageType.REGISTRATION_REQUEST,
        )

    async def deregister(self) -> None:
        """
        Deregister agent from transport and core service.
        Component deregistration is a direct call, not message-based.
        """
        # Deregister from transport first (direct call, always works)
        await self.transport.deregister_component(self.agent_id)
        
        # Try to notify core, but don't fail if transport is shutting down
        try:
            await self.send_message(
                target_id=self.core_id,
                content={"status": AgentStatus.INACTIVE},
                message_type=MessageType.DEREGISTRATION_REQUEST,
            )
        except RuntimeError:
            # Transport may be shutting down, notification is best-effort
            pass

    async def discover_agents(self, capabilities: List[str] | None = None) -> None:
        """
        Discover agents with specified capabilities.
        """

        await self.send_message(
            content={"capabilities": capabilities or []},
            target_id=self.core_id,
            message_type=MessageType.DISCOVERY_REQUEST,
        )
