import asyncio
from abc import ABC, abstractmethod
from typing import (
    Any,
    ClassVar,
    Dict,
    Set,
    Type,
    TypeVar,
)

from mas.logger import get_logger
from mas.mas import MASContext
from mas.protocol import AgentRuntimeMetric, AgentStatus, Message
from mas.protocol.types import MessageType
from mas.sdk.state import AgentState, StateCallback, StateManager

from .runtime import AgentRuntime

logger = get_logger()

T = TypeVar("T", bound="Agent")
S = TypeVar("S", bound=AgentState)


class Agent(ABC):
    """Base agent class that works with runtime."""

    # Add class variable type annotations
    agent_id: ClassVar[str]
    metadata: ClassVar[Dict[str, Any]]
    capabilities: ClassVar[Set[str]]
    state_model: ClassVar[Type[AgentState]]

    def __init__(
        self,
        runtime: AgentRuntime,
        state_model: Type[AgentState] = AgentState,
    ) -> None:
        self.runtime = runtime
        self._loop = asyncio.get_running_loop()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._metrics = AgentRuntimeMetric()
        self._metric_lock = asyncio.Lock()
        self._state_model = state_model
        self._state_manager = StateManager(state_model)
        self._subscribed_event = asyncio.Event()

    @classmethod
    async def create_agent(
        cls: Type[T],
        mas_context: MASContext,
    ) -> T:
        """
        Build and initialize an agent instance.

        Args:
            mas_context: The Multi-Agent System context

        Returns:
            An initialized agent instance
        """
        runtime = AgentRuntime(
            agent_id=cls.agent_id,
            metadata=cls.metadata,
            transport=mas_context.transport,
            persistence=mas_context.persistence,
            capabilities=set(cls.capabilities),
        )

        agent = cls(runtime, state_model=cls.state_model)
        await agent.start()
        return agent

    @property
    def state(self) -> AgentState:
        """Get current agent state"""
        return self._state_manager.state

    async def update_state(self, data: Dict[str, Any]) -> None:
        """Update agent state"""
        await self._state_manager.update(data)

    async def reset_state(self) -> None:
        """Reset agent state"""
        await self._state_manager.reset()

    def subscribe_to_state(self, callback: StateCallback[AgentState]) -> None:
        """Subscribe to state changes"""
        self._state_manager.subscribe(callback)

    def unsubscribe_from_state(self, callback: StateCallback[AgentState]) -> None:
        """Unsubscribe from state changes"""
        self._state_manager.unsubscribe(callback)

    @property
    def id(self) -> str:
        return self.runtime.agent_id
    

    async def send_message(self, message: Message) -> None:
        """Send a message to another agent."""
        await self.runtime.send_message(
            content=message.payload,
            target_id=message.target_id,
            message_type=message.message_type,
        )

    async def start(self) -> None:
        """Start the agent."""
        if self._running:
            return
        try:
            self._running = True
            self._tasks.append(
                self._loop.create_task(
                    self._message_stream(),
                    name=f"{self.id}_message_stream",
                )
            )
            # Ensure the message stream has subscribed before proceeding
            try:
                await asyncio.wait_for(self._subscribed_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                # Proceed even if subscription confirmation is delayed
                pass
            await self.runtime.register()
            await self.on_start()
        except Exception as e:
            logger.error(f"Failed to start agent {self.id}: {e}")
            await self.stop()

    async def stop(self) -> None:
        """Stop the agent."""
        if not self._running:
            return
        try:
            self._running = False
            await self.runtime.deregister()
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.on_stop()
        except Exception as e:
            logger.error(f"Error stopping agent {self.id}: {e}")
            raise

    async def on_start(self) -> None:
        """Called when agent starts."""
        pass

    async def on_stop(self) -> None:
        """Called when agent stops."""
        pass

    @abstractmethod
    async def on_message(self, message: Message) -> None:
        """Handle incoming messages."""
        pass

    async def _message_stream(self) -> None:
        """Handle messages to other agents."""
        try:
            async with self.runtime.transport.message_stream(
                self.id,
                self.id,
            ) as stream:
                # Signal that subscription is active
                self._subscribed_event.set()
                async for message in stream:  # type: ignore
                    # Check if shutdown was signaled
                    if not self._running:
                        logger.info(f"{self.id} shutting down gracefully")
                        await self.runtime.deregister()
                        break
                    
                    try:
                        if message.target_id != self.id:
                            continue

                        async with self._metric_lock:
                            self._metrics.messages_received += 1
                            if message.sender_id == "core":
                                await self._core_message_handler(message)
                            else:
                                await self.on_message(message)
                        
                        # Check again after message processing
                        if not self._running:
                            logger.info(f"{self.id} shutting down gracefully")
                            await self.runtime.deregister()
                            break
                    except asyncio.CancelledError:
                        logger.debug(f"Message processing cancelled for {self.id}")
                        raise
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")

        except asyncio.CancelledError:
            logger.debug(f"Message stream cancelled for {self.id}")
            raise
        except Exception as e:
            if self._running:
                logger.error(f"Error processing message: {e}")

    async def _core_message_handler(self, message: Message) -> None:
        """Handle messages from the core."""
        match message.message_type:
            case MessageType.REGISTRATION_RESPONSE:
                try:
                    _ = message.payload["token"]
                    logger.info(f"{self.id} is registered")
                except KeyError:
                    logger.error("Failed to register agent")
                    # Signal shutdown instead of calling stop() directly
                    self._running = False
            case MessageType.STATUS_UPDATE_RESPONSE:
                try:
                    status = message.payload["status"]
                    if status == "shutdown":
                        logger.debug(f"{self.id} received shutdown message from core")
                        # Signal shutdown instead of calling stop() directly to avoid deadlock
                        self._running = False
                except KeyError:
                    logger.error("Failed to process status update response")
            case MessageType.HEALTH_CHECK:
                payload = {
                    **self._metrics.model_dump(),
                    "status": AgentStatus.ACTIVE.value,
                }
                await self.runtime.send_message(
                    content=payload,
                    target_id=message.sender_id,
                    message_type=MessageType.HEALTH_CHECK_RESPONSE,
                )
            case _:
                logger.error(f"{self.id} received unknown message type: {message}")
