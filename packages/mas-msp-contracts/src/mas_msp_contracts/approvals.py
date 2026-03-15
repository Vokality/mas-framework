"""Approval workflow contracts."""

from __future__ import annotations

from pydantic import Field

from ._base import ContractModel
from .enums import ApprovalDecisionValue
from .types import (
    ApprovalId,
    ClientId,
    FabricId,
    IncidentId,
    JSONObject,
    NonEmptyStr,
    UTCDatetime,
)


class ApprovalRequested(ContractModel):
    """Approval request record."""

    approval_id: ApprovalId
    client_id: ClientId
    fabric_id: FabricId
    incident_id: IncidentId | None = None
    action_kind: NonEmptyStr
    title: NonEmptyStr
    requested_at: UTCDatetime
    expires_at: UTCDatetime
    requested_by_agent: NonEmptyStr
    payload: JSONObject = Field(default_factory=dict)
    risk_summary: NonEmptyStr


class ApprovalDecision(ContractModel):
    """Human approval decision."""

    approval_id: ApprovalId
    decided_by_user_id: NonEmptyStr
    decision: ApprovalDecisionValue
    decided_at: UTCDatetime
    reason: NonEmptyStr | None = None


__all__ = ["ApprovalDecision", "ApprovalRequested"]
