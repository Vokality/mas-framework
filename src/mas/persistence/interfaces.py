from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from mas.protocol import Agent, AgentStatus, Message


class IPersistenceProvider(ABC):
    """Interface that must be implemented by persistence providers."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the persistence provider."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup persistence resources."""
        pass

    # Agent operations
    @abstractmethod
    async def create_agent(self, agent: Agent) -> Agent:
        """Create a new agent."""
        pass

    @abstractmethod
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        pass

    @abstractmethod
    async def update_agent_status(self, agent_id: str, status: AgentStatus) -> bool:
        """Update agent status."""
        pass

    @abstractmethod
    async def get_active_agents(self) -> List[Agent]:
        """Get all active agents."""
        pass

    @abstractmethod
    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent."""
        pass

    # Message operations
    @abstractmethod
    async def store_message(self, message: Message) -> None:
        """Store a message."""
        pass

    @abstractmethod
    async def get_message(self, message_id: UUID) -> Optional[Message]:
        """Get message by ID."""
        pass

    @abstractmethod
    async def get_agent_messages(self, agent_id: str) -> List[Message]:
        """Get messages for an agent."""
        pass
