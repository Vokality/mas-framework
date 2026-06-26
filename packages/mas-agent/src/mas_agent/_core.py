"""Shared instance contract for the MAS agent concern mixins.

``Agent`` is composed from focused mixins (transport, messaging, handler
registration). They all need the same handful of instance attributes, which are
assigned once in ``Agent.__init__``. Declaring that shared shape here lets each
mixin be type-checked in isolation while still composing into a single object.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import grpc.aio as grpc_aio
from mas_core import EnvelopeMessage
from mas_proto.runtime.v1 import (
    runtime_pb2 as mas_pb2,
)
from mas_proto.runtime.v1 import (
    runtime_pb2_grpc as mas_pb2_grpc,
)

from .config import EARLY_REPLY_MAX_ENTRIES, EARLY_REPLY_TTL_SECONDS, TlsClientConfig

# Public alias so external imports continue to work.
AgentMessage = EnvelopeMessage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingRequest:
    """A request awaiting its reply, bound to the agent it was sent to.

    ``target_id`` is the only agent authorized to answer this request. Binding
    the reply to the request target stops an unrelated (or malicious) agent from
    resolving the future just by knowing/guessing the correlation id.
    """

    future: asyncio.Future[AgentMessage]
    target_id: str


class EarlyReplies:
    """Bounded, TTL-pruned buffer for replies that arrive before their request.

    A reply can land on the transport stream before :meth:`Agent.request`
    finishes registering its future (a benign race). We stash it here keyed by
    correlation id so ``request`` can pick it up. Late replies that arrive
    *after* a request has timed out or been cancelled also land here and would
    otherwise never be reclaimed, so the buffer caps its size (oldest evicted
    first) and expires entries after a TTL. Dict-like ``buf[cid] = msg`` /
    ``buf.pop(cid, None)`` access is supported so call sites read naturally.
    """

    def __init__(
        self,
        *,
        max_entries: int = EARLY_REPLY_MAX_ENTRIES,
        ttl_seconds: float = EARLY_REPLY_TTL_SECONDS,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[AgentMessage, float]] = OrderedDict()

    def put(
        self, correlation_id: str, msg: AgentMessage, *, now: float | None = None
    ) -> None:
        """Store a reply, pruning expired entries and bounding total size."""
        now = time.monotonic() if now is None else now
        self._prune(now)
        self._items[correlation_id] = (msg, now + self._ttl_seconds)
        self._items.move_to_end(correlation_id)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def pop(
        self,
        correlation_id: str,
        default: AgentMessage | None = None,
        *,
        now: float | None = None,
        expire: bool = True,
    ) -> AgentMessage | None:
        """Remove and return a reply, else ``default``.

        With ``expire=True`` (default) a reply older than its TTL is treated as
        absent. A caller actively awaiting a specific correlation id should pass
        ``expire=False``: the TTL only bounds memory for unclaimed entries, and
        the reply is still the correct one for that request no matter how long
        the round-trip took.
        """
        now = time.monotonic() if now is None else now
        item = self._items.pop(correlation_id, None)
        if item is None:
            return default
        msg, deadline = item
        if expire and deadline < now:
            return default
        return msg

    def _prune(self, now: float) -> None:
        """Drop entries whose TTL has elapsed.

        Deadlines increase with insertion order (``now`` is monotonic and the
        TTL is constant), so expired entries are always an oldest-first prefix.
        Pruning that prefix is amortised O(expired) instead of scanning the
        whole buffer on every ``put``.
        """
        while self._items:
            cid, (_, deadline) = next(iter(self._items.items()))
            if deadline >= now:
                break
            del self._items[cid]

    def __setitem__(self, correlation_id: str, msg: AgentMessage) -> None:
        self.put(correlation_id, msg)

    def __contains__(self, correlation_id: str) -> bool:
        item = self._items.get(correlation_id)
        return item is not None and item[1] >= time.monotonic()

    def __len__(self) -> int:
        return len(self._items)


class AgentCore:
    """Shared instance state and low-level helpers for the agent mixins."""

    id: str
    instance_id: str
    capabilities: list[str]
    server_addr: str
    tls: TlsClientConfig | None

    _running: bool
    _channel: grpc_aio.Channel | None
    _stub: mas_pb2_grpc.RuntimeServiceStub | None
    _transport_ready: asyncio.Event
    _outgoing: asyncio.Queue[mas_pb2.ClientEvent]
    _transport_task: asyncio.Task[None] | None
    _pending_requests: dict[str, PendingRequest]
    _early_replies: EarlyReplies
    _handler_tasks: set[asyncio.Task[None]]

    def _require_stub(self) -> mas_pb2_grpc.RuntimeServiceStub:
        """Return the gRPC stub if connected."""
        if not self._stub:
            raise RuntimeError("Agent not started")
        return self._stub

    def _reply_authorized(
        self, reply: AgentMessage, expected_sender: str, *, source: str
    ) -> bool:
        """Whether ``reply`` may resolve a request sent to ``expected_sender``.

        A reply must come from the agent the request targeted; binding it to the
        request target stops an unrelated agent from resolving a pending future
        by knowing or guessing the correlation id. The trusted server enforces
        this authoritatively — this is defense-in-depth. Single source of truth
        so the live-delivery and early-reply paths cannot drift. Mismatches are
        logged and rejected.
        """
        if reply.sender_id == expected_sender:
            return True
        logger.warning(
            "Rejected reply with unexpected sender",
            extra={
                "agent_id": self.id,
                "expected_sender": expected_sender,
                "actual_sender": reply.sender_id,
                "correlation_id": reply.meta.correlation_id,
                "source": source,
            },
        )
        return False

    # --- User-overridable lifecycle hooks ---

    async def on_start(self) -> None:
        """Called after transport is ready."""

    async def on_stop(self) -> None:
        """Called before shutting down."""

    async def on_message(self, message: AgentMessage) -> None:
        """Fallback handler when no typed handler is registered."""

    async def _dispatch_typed(self, msg: AgentMessage) -> bool:
        """Dispatch to a typed handler; implemented by HandlerRegistryMixin."""
        raise NotImplementedError
