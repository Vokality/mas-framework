"""Input models for Windows host ingest and polling."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from mas_msp_contracts import CredentialRef, JSONObject, NonEmptyStr, UTCDatetime
from mas_msp_contracts._base import ContractModel


class WindowsEventRecord(ContractModel):
    """Normalized Windows Event Forwarding envelope before alert mapping."""

    client_id: str
    fabric_id: str
    occurred_at: UTCDatetime
    message: NonEmptyStr
    level: NonEmptyStr
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr | None = None
    edition: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    service_name: NonEmptyStr | None = None
    source_kind: NonEmptyStr = "wef"
    tags: list[NonEmptyStr] = Field(default_factory=list)


class WindowsPollingTarget(ContractModel):
    """Target metadata for constrained Windows polling."""

    client_id: str
    fabric_id: str
    credential_ref: CredentialRef
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr
    edition: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class WindowsPollObservation(ContractModel):
    """Normalized observation produced by a Windows poller implementation."""

    collected_at: UTCDatetime
    metrics: JSONObject = Field(default_factory=dict)
    services: list[JSONObject] = Field(default_factory=list)
    findings: list[JSONObject] = Field(default_factory=list)


class WindowsHostPoller(Protocol):
    """Transport abstraction for future constrained Windows pollers."""

    async def poll(self, target: WindowsPollingTarget) -> WindowsPollObservation: ...


__all__ = [
    "WindowsEventRecord",
    "WindowsHostPoller",
    "WindowsPollObservation",
    "WindowsPollingTarget",
]
