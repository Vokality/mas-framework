"""SSE event log and live fan-out helpers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.db.models import OpsStreamEvent
from mas_ops_api.settings import OpsApiSettings


StreamScopeKind = Literal["portfolio", "client", "incident", "chat"]

PORTFOLIO_EVENT_NAMES = frozenset({"portfolio.updated", "client.updated"})
CLIENT_EVENT_NAMES = frozenset(
    {"client.updated", "incident.updated", "asset.updated", "activity.appended"}
)
INCIDENT_EVENT_NAMES = frozenset(
    {"incident.updated", "activity.appended", "approval.requested", "approval.resolved"}
)
CHAT_EVENT_NAMES = frozenset(
    {"chat.delta", "chat.completed", "approval.requested", "approval.resolved"}
)


@dataclass(frozen=True, slots=True)
class StreamScope:
    """Scope selector for replay and live SSE fan-out."""

    kind: StreamScopeKind
    client_ids: frozenset[str] = frozenset()
    client_id: str | None = None
    incident_id: str | None = None
    chat_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class StreamRecord:
    """Serializable representation of one committed stream event."""

    stream_id: int
    event_name: str
    event_id: str
    client_id: str | None
    incident_id: str | None
    chat_session_id: str | None
    subject_type: str
    subject_id: str
    occurred_at: datetime
    payload: dict[str, object]

    @classmethod
    def from_model(cls, event: OpsStreamEvent) -> "StreamRecord":
        """Build a stream record from a committed ORM model."""

        return cls(
            stream_id=event.stream_id,
            event_name=event.event_name,
            event_id=event.event_id,
            client_id=event.client_id,
            incident_id=event.incident_id,
            chat_session_id=event.chat_session_id,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            occurred_at=event.occurred_at.astimezone(UTC),
            payload=event.payload,
        )


class StreamBroker:
    """In-process live fan-out broker layered on top of the committed event log."""

    def __init__(self) -> None:
        self._next_subscriber_id = 0
        self._subscribers: dict[int, asyncio.Queue[StreamRecord]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self) -> tuple[int, asyncio.Queue[StreamRecord]]:
        """Register a live subscriber queue."""

        async with self._lock:
            self._next_subscriber_id += 1
            subscriber_id = self._next_subscriber_id
            queue: asyncio.Queue[StreamRecord] = asyncio.Queue()
            self._subscribers[subscriber_id] = queue
            return subscriber_id, queue

    async def unsubscribe(self, subscriber_id: int) -> None:
        """Remove a subscriber queue."""

        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

    async def publish(self, record: StreamRecord) -> None:
        """Fan out a committed event to all live subscribers."""

        async with self._lock:
            subscribers = tuple(self._subscribers.values())
        for queue in subscribers:
            await queue.put(record)


class StreamService:
    """Own committed stream events, replay filters, and SSE serialization."""

    def __init__(
        self, settings: OpsApiSettings, broker: StreamBroker | None = None
    ) -> None:
        self._settings = settings
        self._broker = broker or StreamBroker()

    @property
    def broker(self) -> StreamBroker:
        """Expose the live broker for SSE route handlers."""

        return self._broker

    def build_event(
        self,
        *,
        event_name: str,
        client_id: str | None,
        incident_id: str | None,
        chat_session_id: str | None,
        subject_type: str,
        subject_id: str,
        payload: dict[str, object],
        occurred_at: datetime | None = None,
    ) -> OpsStreamEvent:
        """Create an uncommitted stream event row."""

        event_time = (occurred_at or datetime.now(UTC)).astimezone(UTC)
        return OpsStreamEvent(
            event_id=str(uuid4()),
            event_name=event_name,
            client_id=client_id,
            incident_id=incident_id,
            chat_session_id=chat_session_id,
            subject_type=subject_type,
            subject_id=subject_id,
            occurred_at=event_time,
            payload=payload,
        )

    async def publish(self, event: OpsStreamEvent) -> None:
        """Publish a committed ORM row to live subscribers."""

        await self._broker.publish(StreamRecord.from_model(event))

    async def load_replay(
        self,
        session: AsyncSession,
        *,
        scope: StreamScope,
        last_event_id: str | None,
    ) -> list[StreamRecord]:
        """Load committed replay rows for a scope after an optional event id."""

        stmt = self._base_query(scope)
        if last_event_id is not None:
            stmt = stmt.where(OpsStreamEvent.stream_id > int(last_event_id))
        rows = (
            await session.scalars(stmt.order_by(OpsStreamEvent.stream_id.asc()))
        ).all()
        return [StreamRecord.from_model(row) for row in rows]

    def matches_scope(self, record: StreamRecord, scope: StreamScope) -> bool:
        """Return whether a live event belongs to the target scope."""

        if scope.kind == "portfolio":
            return (
                record.client_id is not None
                and record.client_id in scope.client_ids
                and record.event_name in PORTFOLIO_EVENT_NAMES
            )
        if scope.kind == "client":
            return (
                record.client_id == scope.client_id
                and record.event_name in CLIENT_EVENT_NAMES
            )
        if scope.kind == "incident":
            return (
                record.incident_id == scope.incident_id
                and record.event_name in INCIDENT_EVENT_NAMES
            )
        return (
            record.chat_session_id == scope.chat_session_id
            and record.event_name in CHAT_EVENT_NAMES
        )

    def encode_sse(self, record: StreamRecord) -> str:
        """Encode one stream event as an SSE frame."""

        data = {
            "event_id": record.event_id,
            "client_id": record.client_id,
            "occurred_at": record.occurred_at.isoformat().replace("+00:00", "Z"),
            "subject_type": record.subject_type,
            "subject_id": record.subject_id,
            "payload": record.payload,
        }
        return (
            f"id: {record.stream_id}\n"
            f"event: {record.event_name}\n"
            f"retry: {self._settings.sse_retry_ms}\n"
            f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
        )

    def _base_query(self, scope: StreamScope) -> Select[tuple[OpsStreamEvent]]:
        stmt = select(OpsStreamEvent)
        if scope.kind == "portfolio":
            return stmt.where(
                OpsStreamEvent.client_id.in_(scope.client_ids),
                OpsStreamEvent.event_name.in_(PORTFOLIO_EVENT_NAMES),
            )
        if scope.kind == "client":
            return stmt.where(
                OpsStreamEvent.client_id == scope.client_id,
                OpsStreamEvent.event_name.in_(CLIENT_EVENT_NAMES),
            )
        if scope.kind == "incident":
            return stmt.where(
                OpsStreamEvent.incident_id == scope.incident_id,
                OpsStreamEvent.event_name.in_(INCIDENT_EVENT_NAMES),
            )
        return stmt.where(
            OpsStreamEvent.chat_session_id == scope.chat_session_id,
            OpsStreamEvent.event_name.in_(CHAT_EVENT_NAMES),
        )


__all__ = [
    "CLIENT_EVENT_NAMES",
    "CHAT_EVENT_NAMES",
    "INCIDENT_EVENT_NAMES",
    "PORTFOLIO_EVENT_NAMES",
    "StreamBroker",
    "StreamRecord",
    "StreamScope",
    "StreamService",
]
