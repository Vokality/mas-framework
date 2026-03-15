"""Portfolio event contracts."""

from __future__ import annotations

from pydantic import Field, PositiveInt

from ._base import ContractModel
from .types import (
    ClientId,
    EventId,
    FabricId,
    JSONObject,
    NonEmptyStr,
    UTCDatetime,
)


class PortfolioEvent(ContractModel):
    """Normalized event emitted from a fabric into the ops plane."""

    event_id: EventId
    client_id: ClientId
    fabric_id: FabricId
    event_type: NonEmptyStr
    subject_type: NonEmptyStr
    subject_id: NonEmptyStr
    occurred_at: UTCDatetime
    payload_version: PositiveInt
    payload: JSONObject = Field(default_factory=dict)


__all__ = ["PortfolioEvent"]
