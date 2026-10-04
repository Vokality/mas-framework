"""Shared MAS server types."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TypedDict

from mas_core import JsonObject
from mas_core.observability import ObservationSettings
from mas_core.sessions import SessionLease, SessionLeaseSettings
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from opentelemetry.context import Context

from .management import ManagementSettings


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Agent allowlist entry and metadata."""

    agent_id: str
    capabilities: list[str]
    metadata: JsonObject

    def __post_init__(self) -> None:
        """Keep identities unambiguous in stream keys and certificate paths."""
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.agent_id) is None:
            raise ValueError("agent_id must contain 1-128 letters, digits, '_' or '-'")


class AgentDiscoveryRecord(TypedDict):
    """Discoverable agent record returned by registry lookups."""

    id: str
    capabilities: list[str]
    metadata: JsonObject
    status: str


@dataclass(frozen=True, slots=True)
class TlsConfig:
    """Server-side TLS credential paths."""

    server_cert_path: str
    server_key_path: str
    client_ca_path: str
    revoked_certificates_path: str | None = None


@dataclass(frozen=True, slots=True)
class MASServerSettings:
    """Configuration for MAS server runtime."""

    listen_addr: str
    tls: TlsConfig
    agents: dict[str, AgentDefinition]

    reclaim_idle_ms: int = 30_000
    reclaim_batch_size: int = 50
    max_in_flight: int = 200
    max_delivery_attempts: int = 5
    management: ManagementSettings | None = None
    session_lease: SessionLeaseSettings = field(default_factory=SessionLeaseSettings)
    broker_id: str | None = None
    observations: ObservationSettings | None = field(
        default_factory=ObservationSettings
    )

    def __post_init__(self) -> None:
        """Reject delivery settings that disable progress or violate bounds."""
        if self.reclaim_idle_ms <= 0:
            raise ValueError("reclaim_idle_ms must be positive")
        if self.reclaim_batch_size <= 0:
            raise ValueError("reclaim_batch_size must be positive")
        if self.max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        if self.max_delivery_attempts <= 0:
            raise ValueError("max_delivery_attempts must be positive")
        if (
            self.broker_id is not None
            and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.broker_id) is None
        ):
            raise ValueError("broker_id must contain 1-128 letters, digits, '_' or '-'")
        if any(key != definition.agent_id for key, definition in self.agents.items()):
            raise ValueError("agent allowlist keys must match their agent_id")


@dataclass(slots=True)
class InflightDelivery:
    """Delivery state tracked while awaiting ACK/NACK."""

    stream_name: str
    group: str
    entry_id: str
    envelope_json: str
    received_at: float
    attempt: int = 1
    consumer: str = ""


@dataclass(frozen=True, slots=True)
class OutboundDelivery:
    """Wire delivery with tracing metadata validated before local queueing."""

    delivery: mas_pb2.Delivery
    message_id: str | None = None
    parent: Context | None = None


@dataclass(slots=True)
class Session:
    """Active agent session for a single instance."""

    agent_id: str
    instance_id: str
    outbound: asyncio.Queue[OutboundDelivery]
    inflight: dict[str, InflightDelivery]
    task: asyncio.Task[None]
    lease: SessionLease
    capacity_changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
