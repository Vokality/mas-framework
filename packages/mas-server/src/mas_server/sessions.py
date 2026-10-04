"""Session lifecycle and inflight tracking."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

import grpc
from mas_core.sessions import (
    SessionLease,
    SessionLeaseConflict,
    SessionLeaseSettings,
    SessionLeaseStore,
)
from redis.asyncio import Redis

from .errors import (
    FailedPreconditionError,
    InvalidArgumentError,
    RpcError,
    UnauthenticatedError,
)
from .types import AgentDefinition, InflightDelivery, OutboundDelivery, Session

_INSTANCE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
logger = logging.getLogger(__name__)


class SessionManager:
    """Manage connected agent sessions."""

    def __init__(
        self,
        *,
        agents: dict[str, AgentDefinition],
        redis: Redis,
        lease_settings: SessionLeaseSettings = SessionLeaseSettings(),
    ) -> None:
        """Initialize session manager with allowlisted agents."""
        self._agents = agents
        self._sessions: dict[tuple[str, str], Session] = {}
        self._lock = asyncio.Lock()
        self.leases = SessionLeaseStore(redis, lease_settings)

    async def connect(
        self,
        *,
        agent_id: str,
        instance_id: str,
        task_factory: Callable[
            [str, str, asyncio.Queue[OutboundDelivery], dict[str, InflightDelivery]],
            asyncio.Task[None],
        ],
    ) -> Session:
        """Create and register a new session."""
        if agent_id not in self._agents:
            raise UnauthenticatedError("agent_not_allowlisted")
        if not _INSTANCE_RE.fullmatch(instance_id):
            raise InvalidArgumentError("invalid_instance_id")

        key = (agent_id, instance_id)
        async with self._lock:
            if key in self._sessions:
                raise FailedPreconditionError("instance_already_connected")

            outbound: asyncio.Queue[OutboundDelivery] = asyncio.Queue(maxsize=500)
            inflight: dict[str, InflightDelivery] = {}
            try:
                lease = await self.leases.acquire(agent_id, instance_id)
            except SessionLeaseConflict as exc:
                raise FailedPreconditionError("instance_already_connected") from exc
            try:
                worker = task_factory(agent_id, instance_id, outbound, inflight)
                task = asyncio.create_task(self._run_owned(lease, worker))
            except BaseException:
                await self.leases.release(lease)
                raise

            session = Session(
                agent_id=agent_id,
                instance_id=instance_id,
                outbound=outbound,
                inflight=inflight,
                task=task,
                lease=lease,
            )
            self._sessions[key] = session
            return session

    async def _run_owned(self, lease: SessionLease, worker: asyncio.Task[None]) -> None:
        """Stop delivery and its transport immediately when renewal fails."""

        async def renew() -> None:
            nonlocal lease
            interval = self.leases.settings.renewal_ms / 1000
            while True:
                await asyncio.sleep(interval)
                async with asyncio.timeout(interval):
                    renewed = await self.leases.renew(lease)
                    session = self._sessions.get((lease.agent_id, lease.instance_id))
                    if renewed is None or session is None:
                        raise RpcError(
                            grpc.StatusCode.UNAVAILABLE, "session_lease_lost"
                        )
                    lease = renewed
                    session.lease = renewed

        renewal = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait(
                (worker, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            for completed in done:
                await completed
        finally:
            worker.cancel()
            renewal.cancel()
            await asyncio.gather(worker, renewal, return_exceptions=True)
            try:
                await self.leases.release(lease)
            except Exception:
                logger.exception(
                    "Unable to release session lease; ownership will expire"
                )

    async def disconnect(
        self, *, agent_id: str, instance_id: str
    ) -> tuple[Session | None, bool]:
        """Remove a session and indicate whether agent still has connected sessions."""
        key = (agent_id, instance_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
            remaining = any(aid == agent_id for aid, _ in self._sessions)
        if session is not None:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
            await self.leases.release(session.lease)
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
        delivery = session.inflight.pop(delivery_id, None)
        if delivery is not None:
            session.capacity_changed.set()
        return delivery

    async def ensure_connected(self, agent_id: str, instance_id: str) -> None:
        """Validate that sender instance has an active session."""
        if not _INSTANCE_RE.fullmatch(instance_id):
            raise InvalidArgumentError("invalid_instance_id")
        if agent_id not in self._agents:
            raise UnauthenticatedError("agent_not_allowlisted")
        session = self._sessions.get((agent_id, instance_id))
        if session is not None:
            if session.lease.live and not session.task.done():
                return
            raise FailedPreconditionError("session_not_connected")
        if await self.leases.owner(agent_id, instance_id) is None:
            raise FailedPreconditionError("session_not_connected")

    def lease(self, agent_id: str, instance_id: str) -> SessionLease:
        """Return this broker's exact ownership for fencing delivery mutations."""
        return self.session(agent_id, instance_id).lease

    def session(self, agent_id: str, instance_id: str) -> Session:
        """Return the exact local session used by its delivery and transport workers."""
        session = self._sessions.get((agent_id, instance_id))
        if session is None:
            raise FailedPreconditionError("session_not_connected")
        return session

    async def snapshot(self) -> list[Session]:
        """Return connected sessions without changing their lifecycle."""
        async with self._lock:
            return list(self._sessions.values())

    async def snapshot_and_clear(self) -> list[Session]:
        """Return all sessions and clear internal state."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        return sessions

    @staticmethod
    def drop_oldest_outbound(
        outbound: asyncio.Queue[OutboundDelivery],
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
            inflight.pop(event.delivery.delivery_id, None)

        return dropped
