from datetime import UTC, datetime
from typing import Dict, List, Optional, override
from uuid import UUID

from mas.protocol import AgentStatus, Message

from .interfaces import Agent, IPersistenceProvider


class InMemoryProvider(IPersistenceProvider):
    """In-memory persistence implementation."""

    def __init__(self) -> None:
        self._agents: Dict[str, Agent] = {}
        self._messages: Dict[UUID, Message] = {}
        self._agent_messages: Dict[str, List[UUID]] = {}

    @override
    async def initialize(self) -> None:
        """Nothing to initialize for in-memory."""
        pass

    @override
    async def cleanup(self) -> None:
        """Clear all data."""
        self._agents.clear()
        self._messages.clear()
        self._agent_messages.clear()

    # Agent operations
    @override
    async def create_agent(self, agent: Agent) -> Agent:
        """Store agent in memory."""
        self._agents[agent.id] = agent
        self._agent_messages[agent.id] = []
        return agent

    @override
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent from memory."""
        return self._agents.get(agent_id)

    @override
    async def update_agent_status(self, agent_id: str, status: AgentStatus) -> bool:
        """Update agent status in memory."""
        if agent := self._agents.get(agent_id):
            agent.status = status
            agent.last_seen = datetime.now(UTC)
            return True
        return False

    @override
    async def get_active_agents(self) -> List[Agent]:
        """Get active agents from memory."""
        return [
            agent
            for agent in self._agents.values()
            if agent.status == AgentStatus.ACTIVE
        ]

    @override
    async def delete_agent(self, agent_id: str) -> bool:
        """Delete agent from memory."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            del self._agent_messages[agent_id]
            return True
        return False

    # Message operations
    @override
    async def store_message(self, message: Message) -> None:
        """Store message in memory."""
        self._messages[message.id] = message

        # Track message for sender
        if message.sender_id in self._agent_messages:
            self._agent_messages[message.sender_id].append(message.id)

        # Track message for recipient
        if message.target_id in self._agent_messages:
            self._agent_messages[message.target_id].append(message.id)

    @override
    async def get_message(self, message_id: UUID) -> Optional[Message]:
        """Get message from memory."""
        return self._messages.get(message_id)

    @override
    async def get_agent_messages(self, agent_id: str) -> List[Message]:
        """Get agent messages from memory."""
        message_ids = self._agent_messages.get(agent_id, [])
        return [
            self._messages[msg_id] for msg_id in message_ids if msg_id in self._messages
        ]
