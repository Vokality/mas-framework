"""Shared enumerations for MSP contracts and workflows."""

from __future__ import annotations

from enum import StrEnum


class AssetKind(StrEnum):
    """Supported managed asset categories."""

    NETWORK_DEVICE = "network_device"
    LINUX_HOST = "linux_host"
    WINDOWS_HOST = "windows_host"


class HealthState(StrEnum):
    """Normalized health states for managed assets."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    """Normalized alert and incident severities."""

    INFO = "info"
    WARNING = "warning"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ChatScope(StrEnum):
    """Supported operator chat scopes."""

    GLOBAL = "global"
    INCIDENT = "incident"


class IncidentState(StrEnum):
    """Canonical incident lifecycle states."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ChatTurnState(StrEnum):
    """Canonical operator chat turn states."""

    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalState(StrEnum):
    """Canonical approval lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    EXECUTED = "executed"


class ApprovalDecisionValue(StrEnum):
    """Human approval decisions."""

    APPROVE = "approve"
    REJECT = "reject"


class ConfigApplyState(StrEnum):
    """Canonical config validation and apply states."""

    PENDING = "pending"
    VALIDATING = "validating"
    APPLYING = "applying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


__all__ = [
    "ApprovalDecisionValue",
    "ApprovalState",
    "AssetKind",
    "ChatScope",
    "ChatTurnState",
    "ConfigApplyState",
    "HealthState",
    "IncidentState",
    "Severity",
]
