"""Read-only Windows diagnostics for Phase 5 host incidents."""

from __future__ import annotations

from mas_msp_contracts import DiagnosticsCollect
from mas_msp_contracts.types import JSONObject

from ..common import (
    HostDiagnosticsExecution,
    HostServiceRegistry,
    build_diagnostics_execution,
    extract_service_name,
)


class WindowsDiagnosticsAgent:
    """Execute deterministic read-only diagnostics for Windows hosts."""

    def __init__(self, *, service_registry: HostServiceRegistry | None = None) -> None:
        self._service_registry = service_registry or HostServiceRegistry()

    async def execute_diagnostics(
        self,
        request: DiagnosticsCollect,
        *,
        recent_activity: list[JSONObject] | None = None,
    ) -> HostDiagnosticsExecution:
        """Execute one deterministic Windows diagnostics request."""

        hinted_service = extract_service_name(recent_activity)
        services = self._service_registry.list_services(
            request.asset,
            hinted_service=hinted_service,
        )
        items = _items_for_profile(
            request.diagnostic_profile,
            services=services,
            hostname=request.asset.hostname or request.asset.asset_id,
            hinted_service=hinted_service,
        )
        summary = (
            f"Windows diagnostics captured {request.diagnostic_profile} evidence for "
            f"{request.asset.hostname or request.asset.asset_id}."
        )
        return build_diagnostics_execution(
            request,
            summary=summary,
            items=items,
            structured_results={
                "platform": "windows",
                "diagnostic_profile": request.diagnostic_profile,
                "requested_actions": list(request.requested_actions),
                "read_only": request.read_only,
            },
        )


def _items_for_profile(
    diagnostic_profile: str,
    *,
    services: list[dict[str, object]],
    hostname: str,
    hinted_service: str | None,
) -> list[JSONObject]:
    items: list[JSONObject] = [
        {
            "kind": "host_identity",
            "platform": "windows",
            "hostname": hostname,
        }
    ]
    if diagnostic_profile == "host.services":
        items.append({"kind": "host_services", "services": services})
        if hinted_service:
            items.append(
                {
                    "kind": "verification_target",
                    "service_name": hinted_service,
                }
            )
        return items
    if diagnostic_profile == "host.event_logs":
        items.append(
            {
                "kind": "event_log_excerpt",
                "entries": [
                    "Service Control Manager: Spooler entered the stopped state",
                    "PrintService: queue backlog increased",
                ],
            }
        )
        return items
    if diagnostic_profile == "host.performance":
        items.append(
            {
                "kind": "performance_counters",
                "cpu_percent": 31,
                "memory_percent": 61,
                "disk_percent": 68,
            }
        )
        return items
    items.append(
        {
            "kind": "host_summary",
            "cpu_percent": 29,
            "memory_percent": 58,
            "disk_percent": 63,
        }
    )
    items.append({"kind": "host_services", "services": services})
    return items


__all__ = ["WindowsDiagnosticsAgent"]
