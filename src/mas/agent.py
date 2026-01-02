"""Simplified Agent SDK."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from types import FunctionType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    Mapping,
    MutableMapping,
    Optional,
    cast,
)

from pydantic import BaseModel

from .protocol import EnvelopeMessage, MessageMeta
from .redis_client import create_redis_client
from .redis_types import AsyncRedisProtocol, PubSubProtocol
from .registry import AgentRecord, AgentRegistry
from .state import StateManager, StateType

if TYPE_CHECKING:
    from .gateway import GatewayService

logger = logging.getLogger(__name__)


# Public alias so external imports continue to work
AgentMessage = EnvelopeMessage
JSONDict = dict[str, Any]
MutableJSONMapping = MutableMapping[str, Any]


class Agent(Generic[StateType]):
    """
    Agent that communicates via the Gateway using Redis Streams.

    Key features:
    - Self-contained (only needs Redis URL)
    - Gateway-mediated messaging (central routing and policy enforcement)
    - Auto-persisted state to Redis (shared across instances)
    - Simple discovery by capabilities
    - Automatic heartbeat monitoring (per-instance)
    - Strongly-typed state via generics
    - Multi-instance support for horizontal scaling

    Multi-instance behavior:
    - Each Agent instance gets a unique instance_id
    - Multiple instances with the same agent_id share the workload
    - Messages are load-balanced across instances via Redis consumer groups
    - Request-response replies are routed to the originating instance
    - State is shared across all instances of the same agent_id

    Usage with typed state and decorator-based handlers:
        class MyState(BaseModel):
            counter: int = 0

        class MyAgent(Agent[MyState]):
            def __init__(self, agent_id: str, redis_url: str):
                super().__init__(agent_id, state_model=MyState, redis_url=redis_url)

            @Agent.on("counter.increment")
            async def handle_increment(self, message: AgentMessage, payload: None):
                # self.state is strongly typed as MyState
                self.state.counter += 1
                await self.update_state({"counter": self.state.counter})

    Horizontal scaling:
        # Run multiple instances of the same agent for parallel processing
        # Each instance automatically joins the consumer group
        python my_agent.py &  # Instance 1
        python my_agent.py &  # Instance 2
        python my_agent.py &  # Instance 3
    """

    def __init__(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        redis_url: str = "redis://localhost:6379",
        state_model: type[StateType] | None = None,
    ):
        """
        Initialize agent.

        Args:
            agent_id: Logical agent identifier (shared across instances for scaling)
            capabilities: List of agent capabilities for discovery
            redis_url: Redis connection URL
            state_model: Optional Pydantic model for typed state.
                        If provided, self.state will be strongly typed.

        Note:
            Each Agent instance automatically generates a unique instance_id.
            Multiple instances with the same agent_id will share workload via
            Redis consumer groups.
        """
        self.id = agent_id
        self.instance_id = uuid.uuid4().hex[:8]  # Unique per instance
        self.capabilities = capabilities or []
        self.redis_url = redis_url

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

        # Gateway client (optional, managed externally)
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
        """Start the agent instance."""
        redis_client = create_redis_client(url=self.redis_url, decode_responses=True)
        self._redis = redis_client
        self._registry = AgentRegistry(redis_client)

        # Register agent instance (idempotent - first instance creates, others join)
        self._token = await self._registry.register(
            self.id, self.instance_id, self.capabilities, metadata=self.get_metadata()
        )
        # Seed heartbeat immediately to avoid inactive status on fast restarts
        await self._registry.update_heartbeat(self.id, self.instance_id)

        # Initialize state manager
        self._state_manager = StateManager(
            self.id, redis_client, state_model=self._state_model
        )
        await self._state_manager.load()

        # Ensure delivery stream consumer group exists and start consumer loop
        # Shared stream for load-balanced message delivery
        delivery_stream = f"agent.stream:{self.id}"
        try:
            await redis_client.xgroup_create(
                delivery_stream, "agents", id="$", mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

        # Instance-specific stream for replies to this instance's requests
        # This ensures request-response works correctly with multi-instance agents
        # Uses the same consumer group name as shared stream for xreadgroup compatibility
        reply_stream = f"agent.stream:{self.id}:{self.instance_id}"
        try:
            await redis_client.xgroup_create(
                reply_stream, "agents", id="$", mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

        self._running = True

        # Start background tasks
        self._tasks.append(asyncio.create_task(self._stream_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

        # Publish instance join event
        instance_count = await self._registry.get_instance_count(self.id)
        if instance_count == 1:
            # First instance - publish REGISTER event
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

        # Always publish INSTANCE_JOIN for observability
        await redis_client.publish(
            "mas.system",
            json.dumps(
                {
                    "type": "INSTANCE_JOIN",
                    "agent_id": self.id,
                    "instance_id": self.instance_id,
                    "instance_count": instance_count,
                }
            ),
        )

        logger.info(
            "Agent instance started",
            extra={"agent_id": self.id, "instance_id": self.instance_id},
        )

        # Signal that transport can begin (registration + subscriptions established)
        self._transport_ready.set()

        # Call user hook
        await self.on_start()

    async def stop(self) -> None:
        """Stop the agent instance."""
        self._running = False

        # Call user hook
        await self.on_stop()

        # Cancel tasks
        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Deregister instance and check if last
        if self._registry:
            # Get instance count before deregistering
            instance_count_before = await self._registry.get_instance_count(self.id)
            await self._registry.deregister(self.id, self.instance_id)
            is_last_instance = instance_count_before <= 1

            # Publish events
            if self._redis:
                # Always publish INSTANCE_LEAVE for observability
                await self._redis.publish(
                    "mas.system",
                    json.dumps(
                        {
                            "type": "INSTANCE_LEAVE",
                            "agent_id": self.id,
                            "instance_id": self.instance_id,
                        }
                    ),
                )

                if is_last_instance:
                    # Last instance - publish DEREGISTER event
                    await self._redis.publish(
                        "mas.system",
                        json.dumps(
                            {
                                "type": "DEREGISTER",
                                "agent_id": self.id,
                            }
                        ),
                    )

        # Note: Don't stop gateway - it's shared across agents
        # Gateway lifecycle is managed externally

        if self._redis:
            await self._redis.aclose()

        logger.info(
            "Agent instance stopped",
            extra={"agent_id": self.id, "instance_id": self.instance_id},
        )

    def set_gateway(self, gateway: "GatewayService") -> None:
        """
        Retained for backward compatibility; no-op in streams-only mode.
        """
        self._gateway = gateway

    async def send(
        self, target_id: str, message_type: str, data: Mapping[str, Any]
    ) -> None:
        """
        Send message to target agent.

        Always routes through the gateway (Redis Streams ingress).

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
        """Route an envelope via the gateway ingress stream."""
        if not self._redis:
            raise RuntimeError("Agent not started")

        # Always route via Redis Streams ingress
        await self._transport_ready.wait()
        if not self._redis:
            raise RuntimeError("Agent not started")
        if not self._token:
            raise RuntimeError("No token available for gateway authentication")
        signing_key = await self._get_signing_key()
        if not signing_key:
            raise RuntimeError("No signing key available for agent; cannot sign message")
        ts = time.time()
        nonce = uuid.uuid4().hex
        envelope_json = message.model_dump_json()
        fields: dict[str, str] = {
            "envelope": envelope_json,
            "agent_id": self.id,
            "token": self._token,
            "timestamp": str(ts),
            "nonce": nonce,
        }

        signature_data = {
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "timestamp": ts,
            "nonce": nonce,
            "payload": message.payload,
        }
        canonical_str = self._canonicalize(signature_data)
        key_bytes = bytes.fromhex(signing_key)
        mac = hmac.new(
            key_bytes,
            canonical_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        fields["signature"] = mac
        fields["alg"] = "HMAC-SHA256"
        await self._redis.xadd("mas.gateway.ingress", fields)
        logger.debug(
            "Message enqueued to gateway ingress",
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
                correlation_id=correlation_id,
                expects_reply=True,
                is_reply=False,
                sender_instance_id=self.instance_id,
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

    def _canonicalize(self, data: Mapping[str, Any]) -> str:
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    async def _get_signing_key(self) -> Optional[str]:
        if not self._redis:
            return None
        return await self._redis.get(f"agent:{self.id}:signing_key")

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

    async def _stream_loop(self) -> None:
        """
        Consume incoming messages from both delivery streams.

        Listens on two streams:
        1. Shared stream (agent.stream:{id}) - load-balanced across all instances
        2. Instance stream (agent.stream:{id}:{instance_id}) - replies to this instance
        """
        if not self._redis:
            return

        # Shared stream for load-balanced messages
        shared_stream = f"agent.stream:{self.id}"
        shared_group = "agents"
        shared_consumer = f"{self.id}-{self.instance_id}"

        # Instance-specific stream for replies
        # Uses same group name "agents" for xreadgroup compatibility
        instance_stream = f"agent.stream:{self.id}:{self.instance_id}"

        try:
            while self._running:
                # Read from both streams simultaneously using same consumer group
                items = await self._redis.xreadgroup(
                    shared_group,
                    shared_consumer,
                    streams={shared_stream: ">", instance_stream: ">"},
                    count=50,
                    block=1000,
                )
                if not items:
                    continue

                for stream_name, messages in items:
                    for entry_id, fields in messages:
                        try:
                            data_json = fields.get("envelope", "")
                            if not data_json:
                                await self._redis.xack(
                                    stream_name, shared_group, entry_id
                                )
                                continue
                            msg = AgentMessage.model_validate_json(data_json)
                            msg.attach_agent(self)

                            # Replies resolve pending requests
                            if msg.meta.is_reply:
                                correlation_id = msg.meta.correlation_id
                                if (
                                    correlation_id
                                    and correlation_id in self._pending_requests
                                ):
                                    future = self._pending_requests.pop(correlation_id)
                                    if not future.done():
                                        future.set_result(msg)
                                    await self._redis.xack(
                                        stream_name, shared_group, entry_id
                                    )
                                    continue

                            asyncio.create_task(
                                self._handle_message_and_ack(
                                    msg,
                                    stream_name,
                                    shared_group,
                                    entry_id,
                                )
                            )
                        except Exception as e:
                            logger.error(
                                "Failed to process stream message",
                                exc_info=e,
                                extra={
                                    "agent_id": self.id,
                                    "instance_id": self.instance_id,
                                    "stream": stream_name,
                                },
                            )
        except asyncio.CancelledError:
            pass

    async def _handle_message_and_ack(
        self,
        msg: AgentMessage,
        stream_name: str,
        group: str,
        entry_id: str,
    ) -> None:
        try:
            await self._handle_message_with_error_handling(msg)
        finally:
            if self._redis:
                try:
                    await self._redis.xack(stream_name, group, entry_id)
                except Exception:
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
        """Send periodic heartbeats for this instance."""
        try:
            while self._running:
                if self._registry:
                    await self._registry.update_heartbeat(self.id, self.instance_id)
                await asyncio.sleep(30)  # Heartbeat every 30 seconds
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                "Heartbeat failed",
                exc_info=e,
                extra={"agent_id": self.id, "instance_id": self.instance_id},
            )

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

            # We rely on function-specific attributes below (e.g. `__qualname__` and
            # setting `_agent_handlers`). Unusual callables can still be registered,
            # but they bypass the deferred class-method registration path.
            if not isinstance(fn, FunctionType):
                registry = dict(getattr(cls, "_handlers", {}))
                registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
                setattr(cls, "_handlers", registry)
                return fn

            # Get the class that owns this method by inspecting __qualname__.
            # Format: "ClassName.method_name"
            qualname_parts = fn.__qualname__.split(".")
            if len(qualname_parts) >= 2:
                # Mark the function for later registration
                if not hasattr(fn, "_agent_handlers"):
                    setattr(fn, "_agent_handlers", [])
                handler_list: list[tuple[str, type[BaseModel] | None]] = getattr(
                    fn, "_agent_handlers"
                )
                handler_list.append((message_type, model))
            else:
                # Fallback to old behavior for non-method functions
                registry = dict(getattr(cls, "_handlers", {}))
                registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
                setattr(cls, "_handlers", registry)
            return fn

        return decorator

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Ensure each subclass gets its own handler registry."""
        super().__init_subclass__(**kwargs)

        # Create a new handlers dict for this subclass
        cls._handlers = {}  # type: ignore[misc]

        # Register all decorated methods from this class and parent classes
        for name in dir(cls):
            try:
                attr = getattr(cls, name)
                if hasattr(attr, "_agent_handlers"):
                    handler_list: list[tuple[str, type[BaseModel] | None]] = getattr(
                        attr, "_agent_handlers"
                    )
                    for message_type, model in handler_list:
                        cls._handlers[message_type] = Agent._HandlerSpec(  # type: ignore[misc]
                            fn=attr, model=model
                        )
            except AttributeError:
                pass

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

        If the original message has a sender_instance_id, the reply is routed
        directly to that instance to ensure request-response works correctly
        with multi-instance agents.
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
                # Preserve the original sender's instance ID for routing
                sender_instance_id=original.meta.sender_instance_id,
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
