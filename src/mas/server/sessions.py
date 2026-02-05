"""Session lifecycle and inflight tracking."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from .._proto.v1 import mas_pb2
from .errors import FailedPreconditionError, InvalidArgumentError, UnauthenticatedError
from .types import AgentDefinition, InflightDelivery, Session

_INSTANCE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class SessionManager:
    """Manage connected agent sessions."""

    def __init__(self, *, agents: dict[str, AgentDefinition]) -> None:
        """Initialize session manager with allowlisted agents."""
        self._agents = agents
        self._sessions: dict[tuple[str, str], Session] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        *,
        agent_id: str,
        instance_id: str,
        task_factory: Callable[
            [str, str, asyncio.Queue[mas_pb2.ServerEvent], dict[str, InflightDelivery]],
            asyncio.Task[None],
        ],
    ) -> Session:
        """Create and register a new session."""
        if agent_id not in self._agents:
            raise UnauthenticatedError("agent_not_allowlisted")
        if not _INSTANCE_RE.match(instance_id):
            raise InvalidArgumentError("invalid_instance_id")

        key = (agent_id, instance_id)
        async with self._lock:
            if key in self._sessions:
                raise FailedPreconditionError("instance_already_connected")

            outbound: asyncio.Queue[mas_pb2.ServerEvent] = asyncio.Queue(maxsize=500)
            inflight: dict[str, InflightDelivery] = {}
            task = task_factory(agent_id, instance_id, outbound, inflight)

            session = Session(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
                task=task,
            )
            self._sessions[key] = session
            return session

    async def disconnect(
        self, *, agent_id: str, instance_id: str
    ) -> tuple[Session | None, bool]:
        """Remove a session and indicate whether agent still has connected sessions."""
        key = (agent_id, instance_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
            remaining = any(aid == agent_id for aid, _ in self._sessions)
        return session, remaining

    async def pop_inflight(
        self,
        *,
        agent_id: str,
        instance_id: str,
        delivery_id: str,
    ) -> InflightDelivery | None:
        """Remove and return an inflight delivery for a session."""
        if not delivery_id:
            return None

        async with self._lock:
            session = self._sessions.get((agent_id, instance_id))
        if not session:
            return None
        return session.inflight.pop(delivery_id, None)

    def ensure_connected(self, agent_id: str, instance_id: str) -> None:
        """Validate that sender instance has an active session."""
        if not _INSTANCE_RE.match(instance_id):
            raise InvalidArgumentError("invalid_instance_id")
        if (agent_id, instance_id) not in self._sessions:
            raise FailedPreconditionError("session_not_connected")

    async def snapshot_and_clear(self) -> list[Session]:
        """Return all sessions and clear internal state."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        return sessions

    @staticmethod
    def drop_oldest_outbound(
        outbound: asyncio.Queue[mas_pb2.ServerEvent],
        inflight: dict[str, InflightDelivery],
    ) -> int:
        """Drop oldest outbound events to make room."""
        if outbound.maxsize <= 0 or not outbound.full():
            return 0

        dropped = 0
        while outbound.full():
            try:
                event = outbound.get_nowait()
            except asyncio.QueueEmpty:
                break

            dropped += 1
            if event.HasField("delivery"):
                inflight.pop(event.delivery.delivery_id, None)

        return dropped
