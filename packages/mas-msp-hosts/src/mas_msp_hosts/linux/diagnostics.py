"""Read-only Linux diagnostics for Phase 5 host incidents."""

from __future__ import annotations

from mas_msp_contracts import DiagnosticsCollect
from mas_msp_contracts.types import JSONObject

from ..common import (
    HostDiagnosticsExecution,
    HostServiceRegistry,
    build_diagnostics_execution,
    extract_service_name,
)
from .backends import (
    LinuxDiagnosticsBackend,
    LinuxDiskSample,
    LinuxServiceSample,
    LinuxSummarySample,
    RegistryLinuxDiagnosticsBackend,
)


class LinuxDiagnosticsAgent:
    """Execute deterministic read-only diagnostics for Linux hosts."""

    def __init__(
        self,
        *,
        backend: LinuxDiagnosticsBackend | None = None,
        service_registry: HostServiceRegistry | None = None,
    ) -> None:
        self._backend = backend or RegistryLinuxDiagnosticsBackend(
            service_registry=service_registry
        )

    async def execute_diagnostics(
        self,
        request: DiagnosticsCollect,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> HostDiagnosticsExecution:
        """Execute one deterministic Linux diagnostics request."""

        hinted_service = extract_service_name(recent_activity)
        summary = await self._backend.collect_summary(request)
        services = await self._backend.collect_services(
            request,
            hinted_service=hinted_service,
        )
        items = _items_for_profile(
            request.diagnostic_profile,
            summary=summary,
            services=services,
            disks=await self._backend.collect_disk(request),
            logs=await self._backend.collect_logs(
                request,
                hinted_service=hinted_service,
            ),
            hinted_service=hinted_service,
        )
        summary = (
            f"Linux diagnostics captured {request.diagnostic_profile} evidence for "
            f"{request.asset.hostname or request.asset.asset_id}."
        )
        return build_diagnostics_execution(
            request,
            summary=summary,
            items=items,
            structured_results={
                "platform": "linux",
                "diagnostic_profile": request.diagnostic_profile,
                "requested_actions": list(request.requested_actions),
                "read_only": request.read_only,
            },
        )


def _items_for_profile(
    diagnostic_profile: str,
    *,
    summary: LinuxSummarySample,
    services: list[LinuxServiceSample],
    disks: list[LinuxDiskSample],
    logs: list[str],
    hinted_service: str | None,
) -> list[JSONObject]:
    items: list[JSONObject] = [
        {
            "kind": "host_identity",
            "platform": summary.platform,
            "hostname": summary.hostname,
        }
    ]
    if diagnostic_profile == "host.services":
        items.append({"kind": "host_services", "services": _service_items(services)})
        if hinted_service:
            items.append(
                {
                    "kind": "verification_target",
                    "service_name": hinted_service,
                }
            )
        return items
    if diagnostic_profile == "host.disk":
        items.append(
            {
                "kind": "disk_usage",
                "filesystems": _disk_items(disks),
            }
        )
        return items
    if diagnostic_profile == "host.logs":
        items.append(
            {
                "kind": "journal_excerpt",
                "entries": logs,
            }
        )
        return items
    items.append(
        {
            "kind": "host_summary",
            "distribution": summary.distribution,
            "kernel": summary.kernel,
            "cpu_percent": summary.cpu_percent,
            "memory_percent": summary.memory_percent,
            "load_average": summary.load_average,
        }
    )
    items.append({"kind": "host_services", "services": _service_items(services)})
    return items


def _service_items(services: list[LinuxServiceSample]) -> list[JSONObject]:
    return [
        {
            "service_name": service.service_name,
            "service_state": service.service_state,
        }
        for service in services
    ]


def _disk_items(disks: list[LinuxDiskSample]) -> list[JSONObject]:
    return [
        {
            "mount": disk.mount,
            "used_percent": disk.used_percent,
        }
        for disk in disks
    ]


__all__ = ["LinuxDiagnosticsAgent"]
