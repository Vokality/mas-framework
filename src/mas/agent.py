"""Simplified Agent SDK."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional, TYPE_CHECKING
from redis.asyncio import Redis
from pydantic import BaseModel, Field

from .registry import AgentRegistry
from .state import StateManager

if TYPE_CHECKING:
    from .gateway import GatewayService

logger = logging.getLogger(__name__)


class AgentMessage(BaseModel):
    """Simple agent message for peer-to-peer communication."""

    sender_id: str
    target_id: str
    payload: dict
    timestamp: float = Field(default_factory=time.time)
    message_id: str = Field(default_factory=lambda: str(time.time_ns()))

    model_config = {"arbitrary_types_allowed": True}

    # Internal reference to agent for reply() method (stored in private attribute)
    def __init__(self, **data: Any):
        super().__init__(**data)
        self._agent: Optional["Agent"] = None

    async def reply(self, payload: dict) -> None:
        """
        Reply to this message with automatic correlation handling.

        This is a convenience method that automatically includes the correlation ID
        from the original request, making request-response patterns simpler.

        Args:
            payload: Response payload dictionary

        Raises:
            RuntimeError: If agent is not available or message doesn't expect reply

        Example:
            ```python
            async def on_message(self, message: AgentMessage):
                if message.expects_reply:
                    result = await self.process(message.payload)
                    await message.reply({"result": result})
            ```
        """
        if not self._agent:
            raise RuntimeError(
                "Cannot reply: message not associated with agent. "
                "This should not happen in normal operation."
            )

        correlation_id = self.payload.get("_correlation_id")
        if not correlation_id:
            raise RuntimeError(
                "Cannot reply: message does not have correlation ID. "
                "Only messages sent via request() can be replied to."
            )

        # Send reply with correlation
        await self._agent.send(
            self.sender_id,
            {
                **payload,
                "_correlation_id": correlation_id,
                "_is_reply": True,
            },
        )

    @property
    def expects_reply(self) -> bool:
        """Check if this message expects a reply."""
        return self.payload.get("_expects_reply", False)

    @property
    def is_reply(self) -> bool:
        """Check if this message is a reply to a previous request."""
        return self.payload.get("_is_reply", False)


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
        use_gateway: bool = False,
        gateway_url: Optional[str] = None,
    ):
        """
        Initialize agent.

        Args:
            agent_id: Unique agent identifier
            capabilities: List of agent capabilities for discovery
            redis_url: Redis connection URL
            state_model: Optional Pydantic model for typed state
            use_gateway: Whether to route messages through gateway
            gateway_url: Gateway service URL (if different from redis_url)
        """
        self.id = agent_id
        self.capabilities = capabilities or []
        self.redis_url = redis_url
        self.use_gateway = use_gateway
        self.gateway_url = gateway_url or redis_url

        # Internal state
        self._redis: Optional[Redis] = None
        self._pubsub = None
        self._token: Optional[str] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        # Transport readiness gate - set once startup completes
        self._transport_ready: asyncio.Event = asyncio.Event()

        # Registry and state
        self._registry: Optional[AgentRegistry] = None
        self._state_manager: Optional[StateManager] = None
        self._state_model = state_model

        # Gateway client (if use_gateway=True)
        self._gateway = None

        # Request-response tracking
        self._pending_requests: dict[str, asyncio.Future[AgentMessage]] = {}

    @property
    def state(self) -> Any:
        """Get current state."""
        return self._state_manager.state if self._state_manager else None

    @property
    def token(self) -> Optional[str]:
        """Get agent authentication token."""
        return self._token

    async def start(self) -> None:
        """Start the agent."""
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        self._registry = AgentRegistry(self._redis)

        # Register agent
        self._token = await self._registry.register(
            self.id, self.capabilities, metadata=self.get_metadata()
        )

        # Initialize state manager
        self._state_manager = StateManager(
            self.id, self._redis, state_model=self._state_model
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
            json.dumps(
                {
                    "type": "REGISTER",
                    "agent_id": self.id,
                    "capabilities": self.capabilities,
                }
            ),
        )

        logger.info("Agent started", extra={"agent_id": self.id})

        # Signal that transport can begin (registration + subscriptions established)
        self._transport_ready.set()

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
                json.dumps(
                    {
                        "type": "DEREGISTER",
                        "agent_id": self.id,
                    }
                ),
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

        # Note: Don't stop gateway - it's shared across agents
        # Gateway lifecycle is managed externally

        if self._redis:
            await self._redis.aclose()

        logger.info("Agent stopped", extra={"agent_id": self.id})

    def set_gateway(self, gateway: "GatewayService") -> None:
        """
        Set gateway instance for message routing.

        Args:
            gateway: GatewayService instance to use for message routing
        """
        self._gateway = gateway

    async def send(self, target_id: str, payload: dict) -> None:
        """
        Send message to target agent.

        Routes through gateway if use_gateway=True, otherwise sends P2P.

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

        if self.use_gateway:
            # Ensure framework signaled readiness before routing via gateway
            await self._transport_ready.wait()
            # Route through gateway
            if not self._gateway:
                raise RuntimeError(
                    "Gateway not configured. Use set_gateway() to configure gateway instance."
                )

            if not self._token:
                raise RuntimeError("No token available for gateway authentication")

            # First and only attempt (framework gates readiness)
            result = await self._gateway.handle_message(message, self._token)

            if not result.success:
                raise RuntimeError(
                    f"Gateway rejected message: {result.decision} - {result.message}"
                )

            logger.debug(
                "Message sent via gateway",
                extra={
                    "from": self.id,
                    "to": target_id,
                    "message_id": message.message_id,
                    "latency_ms": result.latency_ms,
                },
            )
        else:
            # Publish directly to target's channel (peer-to-peer)
            await self._redis.publish(f"agent.{target_id}", message.model_dump_json())

            logger.debug(
                "Message sent (P2P)",
                extra={
                    "from": self.id,
                    "to": target_id,
                    "message_id": message.message_id,
                },
            )

    async def request(
        self, target_id: str, payload: dict, timeout: float = 30.0
    ) -> AgentMessage:
        """
        Send a request and wait for response (request-response pattern).

        This method sends a message and waits for a reply with automatic correlation
        tracking. The responder can use message.reply() to send the response.

        This method does NOT block other message processing - it uses asyncio
        primitives to wait for the response while other messages can be handled
        concurrently.

        Args:
            target_id: Target agent identifier
            payload: Request payload dictionary
            timeout: Maximum time to wait for response in seconds (default: 30.0)

        Returns:
            Response message from the target agent

        Raises:
            RuntimeError: If agent is not started
            asyncio.TimeoutError: If response not received within timeout

        Example:
            ```python
            # Requester side:
            response = await self.request(
                "specialist_agent",
                {"question": "What is the diagnosis?", "symptoms": [...]}
            )
            diagnosis = response.payload.get("diagnosis")

            # Responder side:
            async def on_message(self, message: AgentMessage):
                if message.expects_reply:
                    diagnosis = await self.analyze(message.payload)
                    await message.reply({"diagnosis": diagnosis})
            ```
        """
        if not self._redis:
            raise RuntimeError("Agent not started")

        # Generate unique correlation ID
        correlation_id = str(uuid.uuid4())

        # Create future to wait for response
        future: asyncio.Future[AgentMessage] = asyncio.Future()
        self._pending_requests[correlation_id] = future

        # Send request with correlation metadata
        await self.send(
            target_id,
            {
                **payload,
                "_correlation_id": correlation_id,
                "_expects_reply": True,
            },
        )

        logger.debug(
            "Request sent, waiting for response",
            extra={
                "from": self.id,
                "to": target_id,
                "correlation_id": correlation_id,
                "timeout": timeout,
            },
        )

        try:
            # Wait for response (non-blocking - other messages can be processed)
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(
                "Response received",
                extra={
                    "from": target_id,
                    "to": self.id,
                    "correlation_id": correlation_id,
                },
            )
            return response
        except asyncio.TimeoutError:
            # Cleanup on timeout
            self._pending_requests.pop(correlation_id, None)
            logger.warning(
                "Request timeout",
                extra={
                    "from": self.id,
                    "to": target_id,
                    "correlation_id": correlation_id,
                    "timeout": timeout,
                },
            )
            raise
        except Exception:
            # Cleanup on any error
            self._pending_requests.pop(correlation_id, None)
            raise

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

    async def wait_transport_ready(self, timeout: float | None = None) -> None:
        """
        Wait until the framework signals that transport can begin.

        Args:
            timeout: Optional timeout in seconds to wait.
        """
        if timeout is None:
            await self._transport_ready.wait()
        else:
            await asyncio.wait_for(self._transport_ready.wait(), timeout)

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
                    # Attach agent reference for reply() method
                    msg._agent = self

                    # Check if this is a reply to a pending request
                    if msg.is_reply:
                        correlation_id = msg.payload.get("_correlation_id")
                        if correlation_id and correlation_id in self._pending_requests:
                            # Resolve the pending request future
                            future = self._pending_requests.pop(correlation_id)
                            if not future.done():
                                future.set_result(msg)
                            continue  # Don't call on_message for replies

                    # For all other messages, spawn a task to handle concurrently
                    # This allows the agent to process multiple messages at once
                    # without blocking the message loop
                    asyncio.create_task(self._handle_message_with_error_handling(msg))

                except Exception as e:
                    logger.error(
                        "Failed to process message",
                        exc_info=e,
                        extra={"agent_id": self.id},
                    )
        except asyncio.CancelledError:
            pass

    async def _handle_message_with_error_handling(self, msg: AgentMessage) -> None:
        """
        Handle a message with error handling.

        This is called as a separate task to enable concurrent message processing.
        """
        try:
            await self.on_message(msg)
        except Exception as e:
            logger.error(
                "Failed to handle message",
                exc_info=e,
                extra={
                    "agent_id": self.id,
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                },
            )

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
            logger.error("Heartbeat failed", exc_info=e, extra={"agent_id": self.id})

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
