"""Linux diagnostics backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mas_msp_contracts import DiagnosticsCollect

from ..common import HostServiceRegistry


@dataclass(frozen=True, slots=True)
class LinuxSummarySample:
    """Structured host summary data for one Linux diagnostics request."""

    hostname: str
    platform: str
    distribution: str | None = None
    kernel: str | None = None
    load_average: float | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None


@dataclass(frozen=True, slots=True)
class LinuxServiceSample:
    """Structured service status for one Linux diagnostics request."""

    service_name: str
    service_state: str


@dataclass(frozen=True, slots=True)
class LinuxDiskSample:
    """Structured disk usage for one Linux diagnostics request."""

    mount: str
    used_percent: float


class LinuxDiagnosticsBackend(Protocol):
    """Transport boundary for read-only Linux diagnostics collection."""

    async def collect_summary(self, request: DiagnosticsCollect) -> LinuxSummarySample: ...

    async def collect_services(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[LinuxServiceSample]: ...

    async def collect_disk(self, request: DiagnosticsCollect) -> list[LinuxDiskSample]: ...

    async def collect_logs(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[str]: ...


class RegistryLinuxDiagnosticsBackend:
    """Deterministic Linux diagnostics backend used by the default repo runtime."""

    def __init__(self, *, service_registry: HostServiceRegistry | None = None) -> None:
        self._service_registry = service_registry or HostServiceRegistry()

    async def collect_summary(self, request: DiagnosticsCollect) -> LinuxSummarySample:
        """Return a deterministic host summary for one request."""

        return LinuxSummarySample(
            hostname=request.asset.hostname or request.asset.asset_id,
            platform="linux",
            distribution=request.asset.model,
            load_average=1.4,
            cpu_percent=38.0,
            memory_percent=57.0,
        )

    async def collect_services(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[LinuxServiceSample]:
        """Return deterministic service state for one request."""

        services = self._service_registry.list_services(
            request.asset,
            hinted_service=hinted_service,
        )
        resolved: list[LinuxServiceSample] = []
        for service in services:
            service_name = service.get("service_name")
            service_state = service.get("service_state")
            if not isinstance(service_name, str) or not isinstance(service_state, str):
                continue
            resolved.append(
                LinuxServiceSample(
                    service_name=service_name,
                    service_state=service_state,
                )
            )
        return resolved

    async def collect_disk(self, request: DiagnosticsCollect) -> list[LinuxDiskSample]:
        """Return deterministic disk usage for one request."""

        del request
        return [
            LinuxDiskSample(mount="/", used_percent=72.0),
            LinuxDiskSample(mount="/var", used_percent=64.0),
        ]

    async def collect_logs(
        self,
        request: DiagnosticsCollect,
        *,
        hinted_service: str | None,
    ) -> list[str]:
        """Return deterministic log excerpts for one request."""

        del request
        if hinted_service:
            return [
                f"systemd: {hinted_service} entered failed state",
                f"systemd: {hinted_service} dependency chain recovered",
            ]
        return [
            "systemd: host entered degraded state",
            "systemd: dependency chain recovered",
        ]


__all__ = [
    "LinuxDiagnosticsBackend",
    "LinuxDiskSample",
    "LinuxServiceSample",
    "LinuxSummarySample",
    "RegistryLinuxDiagnosticsBackend",
]
