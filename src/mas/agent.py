"""Simplified Agent SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Generic,
    Mapping,
    MutableMapping,
    Optional,
    TYPE_CHECKING,
    cast,
)

from pydantic import BaseModel

from .registry import AgentRegistry, AgentRecord
from .state import StateManager, StateType
from .protocol import EnvelopeMessage, MessageMeta
from .redis_client import create_redis_client
from .redis_types import AsyncRedisProtocol, PubSubProtocol

if TYPE_CHECKING:
    from .gateway import GatewayService

logger = logging.getLogger(__name__)


# Public alias so external imports continue to work
AgentMessage = EnvelopeMessage
JSONDict = dict[str, Any]
MutableJSONMapping = MutableMapping[str, Any]


class Agent(Generic[StateType]):
    """
    Simplified Agent that communicates peer-to-peer via Redis.

    Key features:
    - Self-contained (only needs Redis URL)
    - Peer-to-peer messaging (no central routing)
    - Auto-persisted state to Redis
    - Simple discovery by capabilities
    - Automatic heartbeat monitoring
    - Strongly-typed state via generics

    Usage with typed state:
        class MyState(BaseModel):
            counter: int = 0

        class MyAgent(Agent[MyState]):
            def __init__(self, agent_id: str, redis_url: str):
                super().__init__(agent_id, state_model=MyState, redis_url=redis_url)

            async def on_message(self, message: AgentMessage):
                # self.state is strongly typed as MyState
                print(f"Counter: {self.state.counter}")
                await self.update_state({"counter": self.state.counter + 1})

    Usage with dict state (backward compatible):
        class MyAgent(Agent[dict[str, Any]]):
            def __init__(self, agent_id: str, redis_url: str):
                super().__init__(agent_id, redis_url=redis_url)
                # state_model defaults to None, so state is dict

            async def on_message(self, message: AgentMessage):
                # self.state is typed as dict
                counter = self.state.get("counter", 0)
                await self.update_state({"counter": counter + 1})
    """

    def __init__(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        redis_url: str = "redis://localhost:6379",
        state_model: type[StateType] | None = None,
        use_gateway: bool = False,
        gateway_url: Optional[str] = None,
    ):
        """
        Initialize agent.

        Args:
            agent_id: Unique agent identifier
            capabilities: List of agent capabilities for discovery
            redis_url: Redis connection URL
            state_model: Optional Pydantic model for typed state.
                        If provided, self.state will be strongly typed.
            use_gateway: Whether to route messages through gateway
            gateway_url: Gateway service URL (if different from redis_url)
        """
        self.id = agent_id
        self.capabilities = capabilities or []
        self.redis_url = redis_url
        self.use_gateway = use_gateway
        self.gateway_url = gateway_url or redis_url

        # Internal state
        self._redis: Optional[AsyncRedisProtocol] = None
        self._pubsub: Optional[PubSubProtocol] = None
        self._token: Optional[str] = None
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        # Transport readiness gate - set once startup completes
        self._transport_ready: asyncio.Event = asyncio.Event()

        # Registry and state
        self._registry: Optional[AgentRegistry] = None
        self._state_manager: Optional[StateManager[StateType]] = None
        self._state_model: type[StateType] | None = state_model

        # Gateway client (if use_gateway=True)
        self._gateway: Optional["GatewayService"] = None

        # Request-response tracking
        self._pending_requests: dict[str, asyncio.Future[AgentMessage]] = {}

    @property
    def state(self) -> StateType:
        """
        Get current state.

        Type is inferred from state_model passed to __init__.
        If state_model is a Pydantic BaseModel, returns that model instance.
        If state_model is None, returns dict.
        """
        if self._state_manager is None:
            raise RuntimeError(
                "Agent not started. State is only available after calling start()."
            )
        return self._state_manager.state

    @property
    def token(self) -> Optional[str]:
        """Get agent authentication token."""
        return self._token

    async def start(self) -> None:
        """Start the agent."""
        redis_client = create_redis_client(url=self.redis_url, decode_responses=True)
        self._redis = redis_client
        self._registry = AgentRegistry(redis_client)

        # Register agent
        self._token = await self._registry.register(
            self.id, self.capabilities, metadata=self.get_metadata()
        )

        # Initialize state manager
        self._state_manager = StateManager(
            self.id, redis_client, state_model=self._state_model
        )
        await self._state_manager.load()

        # Subscribe to agent's channel
        self._pubsub = redis_client.pubsub()
        await self._pubsub.subscribe(f"agent.{self.id}")

        self._running = True

        # Start background tasks
        self._tasks.append(asyncio.create_task(self._message_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

        # Publish registration event
        await redis_client.publish(
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

    async def send(
        self, target_id: str, message_type: str, data: Mapping[str, Any]
    ) -> None:
        """
        Send message to target agent.

        Routes through gateway if use_gateway=True, otherwise sends P2P.

        Args:
            target_id: Target agent identifier
            message_type: Message type identifier
            data: Message payload dictionary
        """
        if not self._redis:
            raise RuntimeError("Agent not started")
        payload = dict(data)
        message = AgentMessage(
            sender_id=self.id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
        )
        await self._send_envelope(message)

    async def _send_envelope(self, message: AgentMessage) -> None:
        """Route an envelope via gateway or P2P."""
        if not self._redis:
            raise RuntimeError("Agent not started")

        if self.use_gateway:
            await self._transport_ready.wait()
            if not self._gateway:
                raise RuntimeError(
                    "Gateway not configured. Use set_gateway() to configure gateway instance."
                )
            if not self._token:
                raise RuntimeError("No token available for gateway authentication")
            result = await self._gateway.handle_message(message, self._token)
            if not result.success:
                raise RuntimeError(
                    f"Gateway rejected message: {result.decision} - {result.message}"
                )
            logger.debug(
                "Message sent via gateway",
                extra={
                    "from": self.id,
                    "to": message.target_id,
                    "message_id": message.message_id,
                    "latency_ms": result.latency_ms,
                },
            )
        else:
            await self._redis.publish(
                f"agent.{message.target_id}", message.model_dump_json()
            )
            logger.debug(
                "Message sent (P2P)",
                extra={
                    "from": self.id,
                    "to": message.target_id,
                    "message_id": message.message_id,
                },
            )

    async def request(
        self,
        target_id: str,
        message_type: str,
        data: Mapping[str, Any],
        timeout: float | None = None,
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
            message_type: Message type identifier
            data: Request payload dictionary
            timeout: Optional maximum seconds to wait for response. When None,
                waits indefinitely.

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
                "diagnosis.request",
                {"question": "What is the diagnosis?", "symptoms": [...]}
            )
            diagnosis = response.data.get("diagnosis")

            # Responder side:
            @Agent.on("diagnosis.request")
            async def handle_diagnosis(self, msg: AgentMessage, payload: Mapping[str, Any]):
                diagnosis = await self.analyze(payload)
                await msg.reply("diagnosis.response", {"diagnosis": diagnosis})
            ```
        """
        if not self._redis:
            raise RuntimeError("Agent not started")

        correlation_id = str(uuid.uuid4())
        future: asyncio.Future[AgentMessage] = asyncio.Future()
        self._pending_requests[correlation_id] = future

        payload = dict(data)
        message = AgentMessage(
            sender_id=self.id,
            target_id=target_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                correlation_id=correlation_id, expects_reply=True, is_reply=False
            ),
        )
        await self._send_envelope(message)

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
            if timeout is None:
                response = await future
            else:
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

    async def request_typed(
        self,
        target_id: str,
        message_type: str,
        data: Mapping[str, Any],
        timeout: float | None = None,
    ) -> AgentMessage:
        """Alias for request() - kept for backward compatibility."""
        return await self.request(target_id, message_type, data, timeout)

    async def discover(
        self, capabilities: list[str] | None = None
    ) -> list[AgentRecord]:
        """
        Discover agents by capabilities.

        Args:
            capabilities: Optional list of required capabilities.
                         If None, returns all active agents.

        Returns:
            List of agent records with id, capabilities, and metadata.
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

    async def update_state(self, updates: Mapping[str, Any]) -> None:
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

                if message.get("type") != "message":
                    continue

                try:
                    msg_data = cast(str, message["data"])
                    msg = AgentMessage.model_validate_json(msg_data)
                    # Attach agent reference for reply() method
                    msg.attach_agent(self)

                    # Check if this is a reply to a pending request
                    if msg.meta.is_reply:
                        correlation_id = msg.meta.correlation_id
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
            dispatched = await self._dispatch_typed(msg)
            if not dispatched:
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
    @dataclass(frozen=True, slots=True)
    class _HandlerSpec:
        fn: Callable[..., Awaitable[None]]
        model: type[BaseModel] | None

    @classmethod
    def on(
        cls, message_type: str, *, model: type[BaseModel] | None = None
    ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """
        Decorator to register a handler for a message_type.
        The handler signature should be: async def handler(self, msg, payload_model)
        If model is None, the handler will receive payload_model=None.
        """
        # Model type is annotated; no runtime check needed

        def decorator(
            fn: Callable[..., Awaitable[None]],
        ) -> Callable[..., Awaitable[None]]:
            if not callable(fn):
                raise TypeError("handler must be callable")
            registry = dict(getattr(cls, "_handlers", {}))
            registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
            setattr(cls, "_handlers", registry)
            return fn

        return decorator

    async def _dispatch_typed(self, msg: AgentMessage) -> bool:
        """
        Validate and dispatch based on message_type registry.
        Returns True if a handler was found and executed.
        """
        registry: dict[str, Agent._HandlerSpec] = getattr(
            self.__class__, "_handlers", {}
        )
        spec = registry.get(msg.message_type)
        if not spec:
            return False
        payload_obj = None
        if spec.model:
            payload_obj = spec.model.model_validate(msg.data)
        await spec.fn(self, msg, payload_obj)
        return True

    async def send_reply_envelope(
        self, original: AgentMessage, message_type: str, data: Mapping[str, Any]
    ) -> None:
        """
        Send a correlated reply to the original message.
        """
        if not original.meta.correlation_id:
            raise RuntimeError("Original message missing correlation_id")

        payload = dict(data)
        reply = AgentMessage(
            sender_id=self.id,
            target_id=original.sender_id,
            message_type=message_type,
            data=payload,
            meta=MessageMeta(
                correlation_id=original.meta.correlation_id,
                expects_reply=False,
                is_reply=True,
            ),
        )
        await self._send_envelope(reply)

    async def _send_reply_envelope(
        self, original: AgentMessage, message_type: str, data: Mapping[str, Any]
    ) -> None:
        """Backward-compatible alias for send_reply_envelope()."""
        await self.send_reply_envelope(original, message_type, data)

    def get_metadata(self) -> JSONDict:
        """
        Override to provide agent metadata.

        Returns:
            Metadata dictionary
        """
        return cast(JSONDict, {})

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
