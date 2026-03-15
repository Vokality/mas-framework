"""Contracts for normalized visibility payloads."""

from __future__ import annotations

from pydantic import Field

from ._base import ContractModel
from .assets import AssetRef
from .enums import HealthState, Severity
from .types import (
    AlertId,
    ClientId,
    FabricId,
    JSONObject,
    NonEmptyStr,
    SnapshotId,
    UTCDatetime,
)


class HealthSnapshot(ContractModel):
    """Normalized polling or health-state payload."""

    snapshot_id: SnapshotId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    source_kind: NonEmptyStr
    collected_at: UTCDatetime
    health_state: HealthState
    metrics: JSONObject = Field(default_factory=dict)
    findings: list[JSONObject] = Field(default_factory=list)


class AlertRaised(ContractModel):
    """Normalized alert payload from push-driven sources."""

    alert_id: AlertId
    client_id: ClientId
    fabric_id: FabricId
    asset: AssetRef
    source_kind: NonEmptyStr
    occurred_at: UTCDatetime
    severity: Severity
    category: NonEmptyStr
    title: NonEmptyStr
    normalized_facts: JSONObject = Field(default_factory=dict)
    raw_reference: JSONObject | None = None


__all__ = ["AlertRaised", "HealthSnapshot"]
