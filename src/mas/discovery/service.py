import asyncio
from datetime import UTC, datetime, timedelta
from typing import List, Optional, Set

from mas.logger import get_logger
from mas.persistence.interfaces import IPersistenceProvider
from mas.protocol import Agent, AgentStatus

logger = get_logger()


class DiscoveryService:
    """Discovery service for agent registration and lookup."""

    def __init__(self, persistence: IPersistenceProvider):
        self.persistence = persistence
        self.active_timeout = timedelta(minutes=5)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._cleanup_lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    async def initialize(self) -> None:
        """Start the discovery service."""
        if self._running:
            return

        logger.debug("Initializing discovery service")
        self._running = True
        self._cleanup_task = self._loop.create_task(
            self._cleanup_inactive_agents(),
            name="discovery_cleanup_task",
        )

    async def _cleanup_inactive_agents(self) -> None:
        """Cleanup inactive agents."""
        while self._running:
            try:
                async with self._cleanup_lock:
                    agents = await self.persistence.get_active_agents()
                    cutoff_time = datetime.now(UTC) - self.active_timeout
                    for agent in agents:
                        if agent.last_seen < cutoff_time:
                            await self.persistence.update_agent_status(
                                agent.id,
                                AgentStatus.INACTIVE,
                            )
                            logger.debug(f"Marked agent {agent.id} as inactive")

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                if self._running:  # Only log if not shutting down
                    logger.error(f"Error in cleanup task: {e}")
                    await asyncio.sleep(5)  # Back off on error

    async def cleanup(self) -> None:
        """Stop the discovery service."""
        if not self._running:
            return

        logger.debug("Stopping discovery service")
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        logger.debug("Discovery service stopped")

    async def register_agent(
        self,
        agent_id: str,
        capabilities: Set[str],
    ) -> str:
        """Register an agent.

        Args:
            agent_id: Unique agent identifier
            capabilities: Set of agent capabilities

        Returns:
            Token for the agent
        """
        if not self._running:
            raise RuntimeError("Discovery service not running")

        # Create a simple token (in practice, use proper token generation)
        token = f"token_{agent_id}_{datetime.now(UTC).timestamp()}"
        await self.persistence.create_agent(
            Agent(
                id=agent_id,
                status=AgentStatus.ACTIVE,
                capabilities=list(capabilities),
                token=token,
                metadata={},
            )
        )
        return token

    async def find_agents(
        self,
        capabilities: Optional[Set[str]] | None = None,
    ) -> List[Agent]:
        """Find agents matching capabilities.

        Args:
            capabilities: Optional set of required capabilities

        Returns:
            List of matching agents
        """
        if not self._running:
            raise RuntimeError("Discovery service not running")

        agents = await self.persistence.get_active_agents()
        if not capabilities:
            return agents

        matching = [
            agent for agent in agents if capabilities.issubset(set(agent.capabilities))
        ]
        return matching

    async def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        """Update agent status.

        Args:
            agent_id: Agent ID
            status: New status

        Returns:
            True if updated successfully
        """
        if not self._running:
            raise RuntimeError("Discovery service not running")

        return await self.persistence.update_agent_status(agent_id, status)

    async def verify_token(self, agent_id: str, token: str) -> bool:
        """Verify agent's token.

        Args:
            agent_id: Agent ID
            token: Token to verify

        Returns:
            True if token is valid
        """
        if not self._running:
            raise RuntimeError("Discovery service not running")

        agent = await self.persistence.get_agent(agent_id)
        return agent is not None and agent.token == token

    async def deregister_agent(self, agent_id: str) -> bool:
        """Remove agent registration.

        Args:
            agent_id: Agent ID to deregister

        Returns:
            True if deregistered successfully
        """
        if not self._running:
            raise RuntimeError("Discovery service not running")

        return await self.persistence.delete_agent(agent_id)
