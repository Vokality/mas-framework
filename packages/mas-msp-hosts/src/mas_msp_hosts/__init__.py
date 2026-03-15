"""MSP host integrations for Linux and Windows visibility and execution."""

from .common import (
    HOST_SERVICE_ACTIONS,
    HostDiagnosticsExecution,
    HostServiceRegistry,
    build_host_asset_ref,
    evaluate_host_health,
    extract_service_name,
)
from .linux import (
    LinuxDiagnosticsAgent,
    LinuxEventIngestAgent,
    LinuxExecutorAgent,
    LinuxHostPoller,
    LinuxJournalEvent,
    LinuxPollObservation,
    LinuxPollingAgent,
    LinuxPollingTarget,
)
from .windows import (
    WindowsDiagnosticsAgent,
    WindowsEventIngestAgent,
    WindowsEventRecord,
    WindowsExecutorAgent,
    WindowsHostPoller,
    WindowsPollObservation,
    WindowsPollingAgent,
    WindowsPollingTarget,
)

__all__ = [
    "HOST_SERVICE_ACTIONS",
    "HostDiagnosticsExecution",
    "HostServiceRegistry",
    "LinuxDiagnosticsAgent",
    "LinuxEventIngestAgent",
    "LinuxExecutorAgent",
    "LinuxHostPoller",
    "LinuxJournalEvent",
    "LinuxPollObservation",
    "LinuxPollingAgent",
    "LinuxPollingTarget",
    "WindowsDiagnosticsAgent",
    "WindowsEventIngestAgent",
    "WindowsEventRecord",
    "WindowsExecutorAgent",
    "WindowsHostPoller",
    "WindowsPollObservation",
    "WindowsPollingAgent",
    "WindowsPollingTarget",
    "build_host_asset_ref",
    "evaluate_host_health",
    "extract_service_name",
]
