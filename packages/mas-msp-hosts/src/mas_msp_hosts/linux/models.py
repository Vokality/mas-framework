"""Input models for Linux host ingest and polling."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from mas_msp_contracts import CredentialRef, JSONObject, NonEmptyStr, UTCDatetime
from mas_msp_contracts._base import ContractModel


class LinuxJournalEvent(ContractModel):
    """Normalized Linux host event before alert mapping."""

    client_id: str
    fabric_id: str
    occurred_at: UTCDatetime
    message: NonEmptyStr
    level: NonEmptyStr
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr | None = None
    distribution: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    service_name: NonEmptyStr | None = None
    source_kind: NonEmptyStr = "journald"
    tags: list[NonEmptyStr] = Field(default_factory=list)


class LinuxPollingTarget(ContractModel):
    """Target metadata for constrained Linux polling."""

    client_id: str
    fabric_id: str
    credential_ref: CredentialRef
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr
    distribution: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class LinuxPollObservation(ContractModel):
    """Normalized observation produced by a Linux poller implementation."""

    collected_at: UTCDatetime
    metrics: JSONObject = Field(default_factory=dict)
    services: list[JSONObject] = Field(default_factory=list)
    findings: list[JSONObject] = Field(default_factory=list)


class LinuxHostPoller(Protocol):
    """Transport abstraction for future constrained Linux pollers."""

    async def poll(self, target: LinuxPollingTarget) -> LinuxPollObservation: ...


__all__ = [
    "LinuxHostPoller",
    "LinuxJournalEvent",
    "LinuxPollObservation",
    "LinuxPollingTarget",
]
