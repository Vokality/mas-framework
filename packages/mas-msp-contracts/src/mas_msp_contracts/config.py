"""Desired-state configuration contracts."""

from __future__ import annotations

from pydantic import Field, PositiveInt

from ._base import ContractModel
from .enums import ConfigApplyState
from .types import (
    ClientId,
    ConfigApplyRunId,
    FabricId,
    JSONObject,
    NonEmptyStr,
    UTCDatetime,
)


class ConfigDesiredState(ContractModel):
    """Declarative configuration record."""

    client_id: ClientId
    fabric_id: FabricId
    desired_state_version: PositiveInt
    tenant_metadata: JSONObject = Field(default_factory=dict)
    policy: JSONObject = Field(default_factory=dict)
    inventory_sources: list[JSONObject] = Field(default_factory=list)
    notification_routes: list[JSONObject] = Field(default_factory=list)


class ConfigValidationResult(ContractModel):
    """Validation output for a desired-state configuration."""

    config_apply_run_id: ConfigApplyRunId
    client_id: ClientId
    desired_state_version: PositiveInt
    status: NonEmptyStr
    errors: list[NonEmptyStr] = Field(default_factory=list)
    warnings: list[NonEmptyStr] = Field(default_factory=list)
    validated_at: UTCDatetime


class ConfigApplyRequested(ContractModel):
    """Desired-state config validation or apply request."""

    config_apply_run_id: ConfigApplyRunId
    client_id: ClientId
    desired_state_version: PositiveInt
    requested_by_user_id: NonEmptyStr
    requested_at: UTCDatetime


class ConfigApplyResult(ContractModel):
    """Desired-state apply lifecycle result."""

    config_apply_run_id: ConfigApplyRunId
    client_id: ClientId
    desired_state_version: PositiveInt
    status: ConfigApplyState
    started_at: UTCDatetime | None = None
    completed_at: UTCDatetime | None = None
    error_summary: NonEmptyStr | None = None


__all__ = [
    "ConfigApplyRequested",
    "ConfigApplyResult",
    "ConfigDesiredState",
    "ConfigValidationResult",
]
