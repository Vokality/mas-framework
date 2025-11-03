"""Simplified Agent SDK."""
import asyncio
import json
import logging
import time
from typing import Any, Optional
from redis.asyncio import Redis
from pydantic import BaseModel, Field

from .registry import AgentRegistry
from .state import StateManager

logger = logging.getLogger(__name__)


class AgentMessage(BaseModel):
    """Simple agent message for peer-to-peer communication."""
    sender_id: str
    target_id: str
    payload: dict
    timestamp: float = Field(default_factory=time.time)
    message_id: str = Field(default_factory=lambda: str(time.time_ns()))


class Agent:
    """
    Simplified Agent that communicates peer-to-peer via Redis.
    
    Key features:
    - Self-contained (only needs Redis URL)
    - Peer-to-peer messaging (no central routing)
    - Auto-persisted state to Redis
    - Simple discovery by capabilities
    - Automatic heartbeat monitoring
    
    Usage:
        class MyAgent(Agent):
            async def on_message(self, message: AgentMessage):
                print(f"Got: {message.payload}")
                await self.send(message.sender_id, {"reply": "thanks"})
        
        agent = MyAgent("my_agent", capabilities=["chat"])
        await agent.start()
        await agent.send("other_agent", {"hello": "world"})
    """
    
    def __init__(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        redis_url: str = "redis://localhost:6379",
        state_model: type[BaseModel] | None = None,
    ):
        """
        Initialize agent.
        
        Args:
            agent_id: Unique agent identifier
            capabilities: List of agent capabilities for discovery
            redis_url: Redis connection URL
            state_model: Optional Pydantic model for typed state
        """
        self.id = agent_id
        self.capabilities = capabilities or []
        self.redis_url = redis_url
        
        # Internal state
        self._redis: Optional[Redis] = None
        self._pubsub = None
        self._token: Optional[str] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        
        # Registry and state
        self._registry: Optional[AgentRegistry] = None
        self._state_manager: Optional[StateManager] = None
        self._state_model = state_model
    
    @property
    def state(self) -> Any:
        """Get current state."""
        return self._state_manager.state if self._state_manager else None
    
    async def start(self) -> None:
        """Start the agent."""
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        self._registry = AgentRegistry(self._redis)
        
        # Register agent
        self._token = await self._registry.register(
            self.id,
            self.capabilities,
            metadata=self.get_metadata()
        )
        
        # Initialize state manager
        self._state_manager = StateManager(
            self.id,
            self._redis,
            state_model=self._state_model
        )
        await self._state_manager.load()
        
        # Subscribe to agent's channel
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(f"agent.{self.id}")
        
        self._running = True
        
        # Start background tasks
        self._tasks.append(asyncio.create_task(self._message_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        
        # Publish registration event
        await self._redis.publish(
            "mas.system",
            json.dumps({
                "type": "REGISTER",
                "agent_id": self.id,
                "capabilities": self.capabilities,
            })
        )
        
        logger.info("Agent started", extra={"agent_id": self.id})
        
        # Call user hook
        await self.on_start()
    
    async def stop(self) -> None:
        """Stop the agent."""
        self._running = False
        
        # Call user hook
        await self.on_stop()
        
        # Publish deregistration event
        if self._redis:
            await self._redis.publish(
                "mas.system",
                json.dumps({
                    "type": "DEREGISTER",
                    "agent_id": self.id,
                })
            )
        
        # Cancel tasks
        for task in self._tasks:
            task.cancel()
        
        await asyncio.gather(*self._tasks, return_exceptions=True)
        
        # Cleanup
        if self._registry:
            await self._registry.deregister(self.id)
        
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
        
        if self._redis:
            await self._redis.aclose()
        
        logger.info("Agent stopped", extra={"agent_id": self.id})
    
    async def send(self, target_id: str, payload: dict) -> None:
        """
        Send message directly to target agent.
        
        Args:
            target_id: Target agent identifier
            payload: Message payload dictionary
        """
        if not self._redis:
            raise RuntimeError("Agent not started")
        
        message = AgentMessage(
            sender_id=self.id,
            target_id=target_id,
            payload=payload,
        )
        
        # Publish directly to target's channel (peer-to-peer)
        await self._redis.publish(
            f"agent.{target_id}",
            message.model_dump_json()
        )
        
        logger.debug(
            "Message sent",
            extra={
                "from": self.id,
                "to": target_id,
                "message_id": message.message_id
            }
        )
    
    async def discover(self, capabilities: list[str] | None = None) -> list[dict]:
        """
        Discover agents by capabilities.
        
        Args:
            capabilities: Optional list of required capabilities.
                         If None, returns all active agents.
        
        Returns:
            List of agent info dictionaries
        """
        if not self._registry:
            raise RuntimeError("Agent not started")
        
        return await self._registry.discover(capabilities)
    
    async def update_state(self, updates: dict) -> None:
        """
        Update agent state.
        
        Args:
            updates: Dictionary of state updates
        """
        if not self._state_manager:
            raise RuntimeError("Agent not started")
        
        await self._state_manager.update(updates)
    
    async def reset_state(self) -> None:
        """Reset state to defaults."""
        if not self._state_manager:
            raise RuntimeError("Agent not started")
        
        await self._state_manager.reset()
    
    async def _message_loop(self) -> None:
        """Listen for incoming messages."""
        if not self._pubsub:
            return
        
        try:
            async for message in self._pubsub.listen():
                if not self._running:
                    break
                
                if message["type"] != "message":
                    continue
                
                try:
                    msg = AgentMessage.model_validate_json(message["data"])
                    await self.on_message(msg)
                except Exception as e:
                    logger.error(
                        "Failed to handle message",
                        exc_info=e,
                        extra={"agent_id": self.id}
                    )
        except asyncio.CancelledError:
            pass
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats."""
        try:
            while self._running:
                if self._registry:
                    await self._registry.update_heartbeat(self.id)
                await asyncio.sleep(30)  # Heartbeat every 30 seconds
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                "Heartbeat failed",
                exc_info=e,
                extra={"agent_id": self.id}
            )
    
    # User-overridable hooks
    
    def get_metadata(self) -> dict:
        """
        Override to provide agent metadata.
        
        Returns:
            Metadata dictionary
        """
        return {}
    
    async def on_start(self) -> None:
        """Called when agent starts. Override to add initialization logic."""
        pass
    
    async def on_stop(self) -> None:
        """Called when agent stops. Override to add cleanup logic."""
        pass
    
    async def on_message(self, message: AgentMessage) -> None:
        """
        Called when message received. Override this to handle messages.
        
        Args:
            message: Received message
        """
        pass
