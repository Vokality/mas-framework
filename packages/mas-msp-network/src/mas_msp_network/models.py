"""Input models for Phase 2 network ingest and polling."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from mas_msp_contracts import CredentialRef, JSONObject, NonEmptyStr, UTCDatetime
from mas_msp_contracts._base import ContractModel


class SyslogEvent(ContractModel):
    """Normalized syslog envelope before vendor parsing."""

    client_id: str
    fabric_id: str
    vendor: NonEmptyStr
    message: NonEmptyStr
    occurred_at: UTCDatetime
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr | None = None
    model: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    serial: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class SnmpTrapEvent(ContractModel):
    """Normalized SNMP trap envelope before vendor parsing."""

    client_id: str
    fabric_id: str
    vendor: NonEmptyStr
    trap_oid: NonEmptyStr
    occurred_at: UTCDatetime
    varbinds: JSONObject = Field(default_factory=dict)
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr | None = None
    model: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    serial: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class SnmpV3Target(ContractModel):
    """Target metadata for SNMPv3 polling."""

    client_id: str
    fabric_id: str
    vendor: NonEmptyStr
    credential_ref: CredentialRef
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr
    model: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    serial: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class SnmpPollObservation(ContractModel):
    """Normalized observation produced by an SNMPv3 poller implementation."""

    collected_at: UTCDatetime
    metrics: JSONObject = Field(default_factory=dict)
    findings: list[JSONObject] = Field(default_factory=list)


class SnmpV3Poller(Protocol):
    """Transport abstraction for future real SNMPv3 polling backends."""

    async def poll(self, target: SnmpV3Target) -> SnmpPollObservation: ...


__all__ = [
    "SnmpPollObservation",
    "SnmpTrapEvent",
    "SnmpV3Poller",
    "SnmpV3Target",
    "SyslogEvent",
]
