"""Shared MAS server types."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .._proto.v1 import mas_pb2


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Agent allowlist entry and metadata."""

    agent_id: str
    capabilities: list[str]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TlsConfig:
    """Server-side TLS credential paths."""

    server_cert_path: str
    server_key_path: str
    client_ca_path: str


@dataclass(frozen=True, slots=True)
class MASServerSettings:
    """Configuration for MAS server runtime."""

    redis_url: str
    listen_addr: str
    tls: TlsConfig
    agents: dict[str, AgentDefinition]

    reclaim_idle_ms: int = 30_000
    reclaim_batch_size: int = 50
    max_in_flight: int = 200


@dataclass(slots=True)
class InflightDelivery:
    """Delivery state tracked while awaiting ACK/NACK."""

    stream_name: str
    group: str
    entry_id: str
    envelope_json: str
    received_at: float


@dataclass(slots=True)
class Session:
    """Active agent session for a single instance."""

    agent_id: str
    instance_id: str
    outbound: asyncio.Queue[mas_pb2.ServerEvent]
    inflight: dict[str, InflightDelivery]
    task: asyncio.Task[None]
