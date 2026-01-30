"""Agent client implementation for MAS."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from types import FunctionType
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    Mapping,
    MutableMapping,
    cast,
)

import grpc
import grpc.aio as grpc_aio
from pydantic import BaseModel

from ._proto.v1 import mas_pb2, mas_pb2_grpc
from .protocol import EnvelopeMessage
from .state import StateType

logger = logging.getLogger(__name__)


# Public alias so external imports continue to work
AgentMessage = EnvelopeMessage
JSONDict = dict[str, Any]
MutableJSONMapping = MutableMapping[str, Any]


@dataclass(frozen=True, slots=True)
class TlsClientConfig:
    """Client mTLS credential paths."""

    root_ca_path: str
    client_cert_path: str
    client_key_path: str


class Agent(Generic[StateType]):
    """Agent client that connects to MAS server over gRPC (no Redis access)."""

    @dataclass(frozen=True, slots=True)
    class _HandlerSpec:
        """Typed handler registration entry."""

        fn: Callable[..., Awaitable[None]]
        model: type[BaseModel] | None

    def __init__(
        self,
        agent_id: str,
        *,
        capabilities: list[str] | None = None,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
        state_model: type[StateType] | None = None,
    ) -> None:
        """Initialize an Agent client."""
        self.id = agent_id
        self.instance_id = uuid.uuid4().hex[:8]
        self.capabilities = capabilities or []
        self.server_addr = server_addr
        self.tls = tls

        self._state_model: type[StateType] | None = state_model
        self._state: StateType | None = None

        self._running = False
        self._channel: grpc_aio.Channel | None = None
        self._stub: mas_pb2_grpc.MasServiceStub | None = None

        self._transport_ready: asyncio.Event = asyncio.Event()
        self._outgoing: asyncio.Queue[mas_pb2.ClientEvent] = asyncio.Queue(maxsize=2000)
        self._transport_task: asyncio.Task[None] | None = None

        self._pending_requests: dict[str, asyncio.Future[AgentMessage]] = {}
        self._early_replies: dict[str, AgentMessage] = {}

    @property
    def state(self) -> StateType:
        """Return the current agent state after startup."""
        if self._state is None:
            raise RuntimeError(
                "Agent not started. State is only available after calling start()."
            )
        return self._state

    async def start(self) -> None:
        """Connect to the server and begin transport loop."""
        if self.tls is None:
            raise RuntimeError(
                "TLS config required. Agents must connect via mTLS to MAS server."
            )

        with open(self.tls.root_ca_path, "rb") as f:
            root_certificates = f.read()
        with open(self.tls.client_key_path, "rb") as f:
            private_key = f.read()
        with open(self.tls.client_cert_path, "rb") as f:
            certificate_chain = f.read()

        creds = grpc.ssl_channel_credentials(
            root_certificates=root_certificates,
            private_key=private_key,
            certificate_chain=certificate_chain,
        )
        channel = grpc_aio.secure_channel(self.server_addr, creds)
        self._channel = channel
        self._stub = mas_pb2_grpc.MasServiceStub(channel)

        self._running = True
        self._transport_task = asyncio.create_task(self._transport_loop())

        await self.wait_transport_ready(timeout=10)
        await self._load_state()
        await self.on_start()

        logger.info(
            "Agent started",
            extra={"agent_id": self.id, "instance_id": self.instance_id},
        )

    async def stop(self) -> None:
        """Stop the transport loop and close the channel."""
        self._running = False
        await self.on_stop()

        if self._transport_task is not None:
            self._transport_task.cancel()
            await asyncio.gather(self._transport_task, return_exceptions=True)
            self._transport_task = None

        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None

        logger.info(
            "Agent stopped",
            extra={"agent_id": self.id, "instance_id": self.instance_id},
        )

    async def wait_transport_ready(self, timeout: float | None = None) -> None:
        """Wait until the server transport is ready."""
        if timeout is None:
            await self._transport_ready.wait()
        else:
            await asyncio.wait_for(self._transport_ready.wait(), timeout)

    async def send(
        self, target_id: str, message_type: str, data: Mapping[str, Any]
    ) -> None:
        """Send a one-way message to another agent."""
        stub = self._require_stub()
        await stub.Send(
            mas_pb2.SendRequest(
                target_id=target_id,
                message_type=message_type,
                data_json=json.dumps(dict(data)),
                instance_id=self.instance_id,
            )
        )

    async def request(
        self,
        target_id: str,
        message_type: str,
        data: Mapping[str, Any],
        timeout: float | None = None,
    ) -> AgentMessage:
        """Send a request and await a reply."""
        stub = self._require_stub()

        timeout_ms = int(timeout * 1000) if timeout is not None else 0
        resp = await stub.Request(
            mas_pb2.RequestRequest(
                target_id=target_id,
                message_type=message_type,
                data_json=json.dumps(dict(data)),
                timeout_ms=timeout_ms,
                instance_id=self.instance_id,
            )
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[AgentMessage] = loop.create_future()
        self._pending_requests[resp.correlation_id] = fut

        early = self._early_replies.pop(resp.correlation_id, None)
        if early is not None and not fut.done():
            fut.set_result(early)

        try:
            if timeout is None:
                return await fut
            return await asyncio.wait_for(fut, timeout)
        finally:
            if resp.correlation_id in self._pending_requests and not fut.done():
                self._pending_requests.pop(resp.correlation_id, None)

    async def send_reply_envelope(
        self, original: AgentMessage, message_type: str, payload: dict[str, Any]
    ) -> None:
        """Send a reply for a previously received message."""
        if not original.meta.correlation_id:
            raise RuntimeError("Cannot reply: missing correlation_id")
        stub = self._require_stub()
        await stub.Reply(
            mas_pb2.ReplyRequest(
                correlation_id=original.meta.correlation_id,
                message_type=message_type,
                data_json=json.dumps(dict(payload)),
                instance_id=self.instance_id,
            )
        )

    async def discover(
        self, capabilities: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return matching agents with optional capability filters."""
        stub = self._require_stub()
        resp = await stub.Discover(
            mas_pb2.DiscoverRequest(capabilities=list(capabilities or []))
        )
        records: list[dict[str, Any]] = []
        for rec in resp.agents:
            records.append(
                {
                    "id": rec.agent_id,
                    "capabilities": list(rec.capabilities),
                    "metadata": json.loads(rec.metadata_json)
                    if rec.metadata_json
                    else {},
                    "status": rec.status,
                }
            )
        return records

    async def update_state(self, updates: Mapping[str, Any]) -> None:
        """Update the remote state with provided fields."""
        stub = self._require_stub()
        current = self.state

        if isinstance(current, BaseModel):
            for k, v in updates.items():
                setattr(current, k, v)
            state_dict = current.model_dump()
        else:
            dict_state = cast(MutableMapping[str, Any], current)
            dict_state.update(dict(updates))
            state_dict = dict(dict_state)

        redis_data: dict[str, str] = {}
        for k, v in state_dict.items():
            if isinstance(v, (dict, list)):
                redis_data[k] = json.dumps(v)
            else:
                redis_data[k] = str(v)

        await stub.UpdateState(mas_pb2.UpdateStateRequest(updates=redis_data))

    async def reset_state(self) -> None:
        """Reset remote state to defaults."""
        stub = self._require_stub()
        await stub.ResetState(mas_pb2.ResetStateRequest())
        if self._state_model is None:
            self._state = cast(StateType, {})
        else:
            self._state = self._state_model()

    async def refresh_state(self) -> None:
        """Reload state from the server."""
        await self._load_state()

    async def on_start(self) -> None:
        """User-overridable hook called after transport is ready."""

    async def on_stop(self) -> None:
        """User-overridable hook called before shutting down."""

    async def on_message(self, message: AgentMessage) -> None:
        """Fallback handler when no typed handler is registered."""

    # --- Transport ---

    async def _transport_loop(self) -> None:
        """Stream client events and handle server deliveries."""
        stub = self._require_stub()

        async def outgoing_iter() -> AsyncIterator[mas_pb2.ClientEvent]:
            """Yield outbound events from the client queue."""
            while True:
                event = await self._outgoing.get()
                yield event

        await self._outgoing.put(
            mas_pb2.ClientEvent(hello=mas_pb2.Hello(instance_id=self.instance_id))
        )

        call = stub.Transport(outgoing_iter())

        try:
            async for event in call:
                if event.HasField("welcome"):
                    self._transport_ready.set()
                    continue

                if event.HasField("delivery"):
                    await self._handle_delivery(event.delivery)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "Transport loop failed",
                exc_info=exc,
                extra={"agent_id": self.id, "instance_id": self.instance_id},
            )

    async def _handle_delivery(self, delivery: mas_pb2.Delivery) -> None:
        """Validate and dispatch a delivery message."""
        try:
            msg = AgentMessage.model_validate_json(delivery.envelope_json)
        except Exception as exc:
            await self._send_nack(
                delivery.delivery_id,
                reason=f"invalid_envelope:{type(exc).__name__}",
                retryable=False,
            )
            return

        msg.attach_agent(self)

        # Replies resolve pending requests immediately.
        if msg.meta.is_reply and msg.meta.correlation_id:
            fut = self._pending_requests.pop(msg.meta.correlation_id, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            else:
                self._early_replies[msg.meta.correlation_id] = msg
            await self._send_ack(delivery.delivery_id)
            return

        asyncio.create_task(self._handle_message_and_ack(delivery.delivery_id, msg))

    async def _handle_message_and_ack(
        self, delivery_id: str, msg: AgentMessage
    ) -> None:
        """Run handlers and ACK/NACK as needed."""
        try:
            dispatched = await self._dispatch_typed(msg)
            if not dispatched:
                await self.on_message(msg)
            await self._send_ack(delivery_id)
        except Exception as exc:
            logger.error(
                "Failed to handle message",
                exc_info=exc,
                extra={
                    "agent_id": self.id,
                    "instance_id": self.instance_id,
                    "message_id": msg.message_id,
                    "sender_id": msg.sender_id,
                },
            )
            await self._send_nack(
                delivery_id,
                reason=f"handler_error:{type(exc).__name__}",
                retryable=False,
            )

    async def _send_ack(self, delivery_id: str) -> None:
        """Send an ACK for a delivery."""
        try:
            await self._outgoing.put(
                mas_pb2.ClientEvent(ack=mas_pb2.Ack(delivery_id=delivery_id))
            )
        except Exception:
            pass

    async def _send_nack(
        self, delivery_id: str, *, reason: str, retryable: bool
    ) -> None:
        """Send a NACK for a delivery."""
        try:
            await self._outgoing.put(
                mas_pb2.ClientEvent(
                    nack=mas_pb2.Nack(
                        delivery_id=delivery_id,
                        reason=reason,
                        retryable=retryable,
                    )
                )
            )
        except Exception:
            pass

    async def _load_state(self) -> None:
        """Load initial agent state from the server."""
        stub = self._require_stub()
        resp = await stub.GetState(mas_pb2.GetStateRequest())
        data = dict(resp.state)

        if data:
            if self._state_model is None:
                self._state = cast(StateType, data)
            else:
                try:
                    self._state = self._state_model(**data)
                except Exception:
                    self._state = self._state_model()
        else:
            if self._state_model is None:
                self._state = cast(StateType, {})
            else:
                self._state = self._state_model()

    def _require_stub(self) -> mas_pb2_grpc.MasServiceStub:
        """Return the gRPC stub if connected."""
        if not self._stub:
            raise RuntimeError("Agent not started")
        return self._stub

    # --- Typed handlers ---

    @classmethod
    def on(
        cls, message_type: str, *, model: type[BaseModel] | None = None
    ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """Decorator to register a handler for a message_type."""

        def decorator(
            fn: Callable[..., Awaitable[None]],
        ) -> Callable[..., Awaitable[None]]:
            """Register a function as a handler for this agent class."""
            if not callable(fn):
                raise TypeError("handler must be callable")

            if not isinstance(fn, FunctionType):
                registry = dict(getattr(cls, "_handlers", {}))
                registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
                setattr(cls, "_handlers", registry)
                return fn

            qualname_parts = fn.__qualname__.split(".")
            if len(qualname_parts) >= 2:
                if not hasattr(fn, "_agent_handlers"):
                    setattr(fn, "_agent_handlers", [])
                handler_list: list[tuple[str, type[BaseModel] | None]] = getattr(
                    fn, "_agent_handlers"
                )
                handler_list.append((message_type, model))
            else:
                registry = dict(getattr(cls, "_handlers", {}))
                registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
                setattr(cls, "_handlers", registry)
            return fn

        return decorator

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Collect handler registrations from subclass methods."""
        super().__init_subclass__(**kwargs)
        cls._handlers = {}

        for name in dir(cls):
            try:
                attr = getattr(cls, name)
                if hasattr(attr, "_agent_handlers"):
                    handler_list: list[tuple[str, type[BaseModel] | None]] = getattr(
                        attr, "_agent_handlers"
                    )
                    for message_type, model in handler_list:
                        cls._handlers[message_type] = Agent._HandlerSpec(
                            fn=attr, model=model
                        )
            except AttributeError:
                pass

    async def _dispatch_typed(self, msg: AgentMessage) -> bool:
        """Dispatch to a typed handler if registered."""
        registry: dict[str, Agent._HandlerSpec] = getattr(
            self.__class__, "_handlers", {}
        )
        spec = registry.get(msg.message_type)
        if not spec:
            return False

        if spec.model is None:
            await spec.fn(self, msg, None)
            return True

        payload_model = spec.model.model_validate(msg.data)
        await spec.fn(self, msg, payload_model)
        return True
