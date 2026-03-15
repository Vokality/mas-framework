"""Shared types for desired-state persistence and run history."""

from __future__ import annotations

from dataclasses import dataclass

from mas_ops_api.db.models import (
    ConfigApplyRun,
    ConfigApplyStepRecord,
    ConfigValidationRun,
)


class DesiredStateVersionError(ValueError):
    """Raised when a config replacement does not satisfy optimistic versioning."""


@dataclass(frozen=True, slots=True)
class DesiredStateInput:
    """Input payload for desired-state replacement."""

    client_id: str
    fabric_id: str
    desired_state_version: int
    tenant_metadata: dict[str, object]
    policy: dict[str, object]
    inventory_sources: list[dict[str, object]]
    notification_routes: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class ConfigRunHistory:
    """Grouped config validation and apply history for one client."""

    validation_runs: list[ConfigValidationRun]
    apply_runs: list[ConfigApplyRun]
    apply_steps: dict[str, list[ConfigApplyStepRecord]]


__all__ = ["ConfigRunHistory", "DesiredStateInput", "DesiredStateVersionError"]
