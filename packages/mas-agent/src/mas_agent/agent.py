"""Agent client implementation for MAS.

The agent client is composed from focused concern mixins:

* :class:`~mas_agent.transport.TransportMixin` — the bidirectional gRPC stream,
  delivery dispatch, and graceful-shutdown draining.
* :class:`~mas_agent.messaging.MessagingMixin` — client-initiated RPCs (send,
  request, reply, discover).
* :class:`~mas_agent.handlers.HandlerRegistryMixin` — ``@on`` handler
  registration and typed dispatch.

This module owns construction, the typed ``state`` accessor, the start/stop
lifecycle, and remote state management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping

import grpc
import grpc.aio as grpc_aio
from mas_core import JsonValue, SpanKind, get_telemetry
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)
from mas_proto.runtime.v1 import (
    runtime_pb2_grpc as mas_pb2_grpc,
)
from pydantic import BaseModel

from ._core import AgentMessage, EarlyReplies, PendingRequest
from .config import TlsClientConfig
from .handlers import HandlerRegistryMixin
from .messaging import MessagingMixin
from .transport import TransportMixin

logger = logging.getLogger(__name__)

# Re-exported so ``from mas_agent.agent import ...`` keeps working.
__all__ = ["Agent", "AgentMessage", "StateReloadError", "TlsClientConfig"]


class StateReloadError(RuntimeError):
    """Raised when persisted state cannot be decoded into the state model.

    Reloading is surfaced rather than silently swallowed: resetting to defaults
    on a decode failure would destroy persisted state and hide the corruption.
    """


class Agent[AgentState: BaseModel = BaseModel](
    TransportMixin, MessagingMixin, HandlerRegistryMixin
):
    """Agent client that connects to MAS server over gRPC (no Redis access)."""

    def __init__(
        self,
        agent_id: str,
        *,
        capabilities: list[str] | None = None,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
        state_model: type[AgentState] | None = None,
    ) -> None:
        """Initialize an Agent client."""
        self.id = agent_id
        self.instance_id = uuid.uuid4().hex[:8]
        self.capabilities = capabilities or []
        self.server_addr = server_addr
        self.tls = tls

        self._state_model: type[AgentState] | None = state_model
        self._state: AgentState | None = None

        self._running = False
        self._channel: grpc_aio.Channel | None = None
        self._stub: mas_pb2_grpc.RuntimeServiceStub | None = None

        self._transport_ready: asyncio.Event = asyncio.Event()
        self._outgoing: asyncio.Queue[mas_pb2.ClientEvent] = asyncio.Queue(maxsize=2000)
        self._transport_task: asyncio.Task[None] | None = None

        self._pending_requests: dict[str, PendingRequest] = {}
        self._early_replies: EarlyReplies = EarlyReplies()
        self._handler_tasks: set[asyncio.Task[None]] = set()

    @property
    def state(self) -> AgentState:
        """Return the current agent state after startup."""
        if self._state_model is None:
            raise RuntimeError(
                "Agent has no state_model. Pass a Pydantic model type to use "
                "typed state."
            )
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

        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.start",
            kind=SpanKind.INTERNAL,
            attributes={"mas.agent_id": self.id, "mas.instance_id": self.instance_id},
        ):
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
            self._stub = mas_pb2_grpc.RuntimeServiceStub(channel)

            try:
                self._running = True
                self._transport_task = asyncio.create_task(self._transport_loop())

                await self.wait_transport_ready(timeout=10)
                await self._load_state()
                await self.on_start()
            except Exception:
                # Don't leak the transport task / open channel if startup fails
                # partway (e.g. _load_state raising on corrupt persisted state).
                await self.stop()
                raise

            logger.info(
                "Agent started",
                extra={"agent_id": self.id, "instance_id": self.instance_id},
            )

    async def stop(self) -> None:
        """Stop the transport loop and close the channel."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.stop",
            kind=SpanKind.INTERNAL,
            attributes={"mas.agent_id": self.id, "mas.instance_id": self.instance_id},
        ):
            self._running = False
            await self.on_stop()

            # Drain in-flight handlers and flush queued ACK/NACK events *before*
            # tearing down the transport. Cancelling the transport first would
            # drop acknowledgements for already-completed work and force the
            # server to redeliver those messages.
            await self._drain_handler_tasks()
            await self._drain_outgoing()

            if self._transport_task is not None:
                self._transport_task.cancel()
                await asyncio.gather(self._transport_task, return_exceptions=True)
                self._transport_task = None

            # The transport loop is stopped now, so no new deliveries can spawn
            # handlers. Cancel any that slipped in during the drain window above
            # rather than leaking them past shutdown.
            await self._cancel_handler_tasks()

            if self._channel is not None:
                await self._channel.close()
                self._channel = None
                self._stub = None

            logger.info(
                "Agent stopped",
                extra={"agent_id": self.id, "instance_id": self.instance_id},
            )

    async def update_state(self, updates: Mapping[str, JsonValue]) -> None:
        """Update the remote state with provided fields."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.update_state",
            kind=SpanKind.CLIENT,
            attributes={"mas.agent_id": self.id},
        ):
            stub = self._require_stub()

            if self._state_model is None:
                raise RuntimeError(
                    "Agent state updates require a Pydantic state_model."
                )

            # Reject unknown fields and re-validate the merged state through the
            # model, so an out-of-type or misspelled update is refused rather
            # than silently persisted (and later surfacing as a StateReloadError).
            # Commit to self._state only after the RPC succeeds, so a failed
            # UpdateState never leaves in-memory state ahead of persisted state.
            unknown = set(updates) - set(self._state_model.model_fields)
            if unknown:
                raise ValueError(f"Unknown state field(s): {sorted(unknown)}")

            merged = self.state.model_dump()
            merged.update(updates)
            updated = self._state_model.model_validate(merged)
            state_dict = updated.model_dump(mode="json")

            # JSON-encode every field so it reloads symmetrically in
            # ``_load_state``. Encoding scalars with ``str()`` would lose
            # fidelity: ``None`` -> ``"None"``, ``True`` -> ``"True"``, and
            # nested values would not round-trip through the typed model.
            redis_data = {k: json.dumps(v) for k, v in state_dict.items()}

            await stub.UpdateState(
                mas_pb2.UpdateStateRequest(updates=redis_data),
                metadata=telemetry.grpc_metadata(),
            )
            self._state = updated

    async def reset_state(self) -> None:
        """Reset remote state to defaults."""
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.reset_state",
            kind=SpanKind.CLIENT,
            attributes={"mas.agent_id": self.id},
        ):
            stub = self._require_stub()
            await stub.ResetState(
                mas_pb2.ResetStateRequest(),
                metadata=telemetry.grpc_metadata(),
            )
            if self._state_model is not None:
                self._state = self._state_model()

    async def refresh_state(self) -> None:
        """Reload state from the server."""
        await self._load_state()

    async def _load_state(self) -> None:
        """Load agent state from the server, decoding it symmetrically.

        Each field was JSON-encoded by :meth:`update_state`; decode it back
        before constructing the model so nested/list/dict/None values round-trip
        with full fidelity. A field that fails to decode or validate raises
        StateReloadError rather than silently resetting persisted state.
        """
        telemetry = get_telemetry()
        with telemetry.start_span(
            "mas.agent.load_state",
            kind=SpanKind.CLIENT,
            attributes={"mas.agent_id": self.id},
        ) as span:
            stub = self._require_stub()
            resp = await stub.GetState(
                mas_pb2.GetStateRequest(),
                metadata=telemetry.grpc_metadata(),
            )
            data = dict(resp.state)

            if self._state_model is None:
                return

            if not data:
                self._state = self._state_model()
                return

            try:
                decoded = {key: json.loads(raw) for key, raw in data.items()}
                self._state = self._state_model(**decoded)
            except Exception as exc:
                # Do not silently reset: that would discard persisted state and
                # mask the corruption. Surface it so the caller can react.
                span.record_exception(exc)
                logger.error(
                    "Failed to reload agent state",
                    exc_info=exc,
                    extra={"agent_id": self.id, "instance_id": self.instance_id},
                )
                raise StateReloadError(
                    f"Could not decode persisted state for agent {self.id!r}"
                ) from exc
