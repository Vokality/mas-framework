"""Approval record models used by the phase-4 controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from mas_msp_contracts import ApprovalRequested, ApprovalState
from mas_msp_contracts.types import JSONObject


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """Persisted approval record visible to fabric and ops-plane workflows."""

    approval_id: str
    client_id: str
    fabric_id: str
    incident_id: str | None
    state: ApprovalState
    action_kind: str
    title: str
    requested_at: datetime
    expires_at: datetime
    requested_by_agent: str
    payload: JSONObject = field(default_factory=dict)
    risk_summary: str = ""
    decided_by_user_id: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    expired_at: datetime | None = None
    cancelled_at: datetime | None = None
    executed_at: datetime | None = None

    @classmethod
    def from_requested(
        cls,
        request: ApprovalRequested,
    ) -> "ApprovalRecord":
        """Build a pending approval record from the request contract."""

        return cls(
            approval_id=request.approval_id,
            client_id=request.client_id,
            fabric_id=request.fabric_id,
            incident_id=request.incident_id,
            state=ApprovalState.PENDING,
            action_kind=request.action_kind,
            title=request.title,
            requested_at=request.requested_at,
            expires_at=request.expires_at,
            requested_by_agent=request.requested_by_agent,
            payload=dict(request.payload),
            risk_summary=request.risk_summary,
        )


ApprovalCancellationActorType = Literal["agent", "system", "user"]


@dataclass(frozen=True, slots=True)
class ApprovalCancellation:
    """Structured cancellation command for one approval request."""

    approval_id: str
    cancelled_at: datetime
    actor_type: ApprovalCancellationActorType
    actor_id: str
    reason: str | None = None


__all__ = [
    "ApprovalCancellation",
    "ApprovalCancellationActorType",
    "ApprovalRecord",
]
