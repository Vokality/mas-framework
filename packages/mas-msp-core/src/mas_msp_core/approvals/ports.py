"""Protocol boundaries for phase-4 approval control."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mas_msp_contracts import ApprovalDecision, ApprovalRequested

from .models import ApprovalCancellation, ApprovalExecutionOutcome, ApprovalRecord


class ApprovalStore(Protocol):
    """Persist approval lifecycle records and transitions."""

    async def create_request(
        self,
        request: ApprovalRequested,
    ) -> ApprovalRecord: ...

    async def get_request(self, approval_id: str) -> ApprovalRecord | None: ...

    async def resolve_request(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalRecord: ...

    async def mark_executed(
        self,
        approval_id: str,
        *,
        executed_at: datetime,
    ) -> ApprovalRecord: ...

    async def cancel_request(
        self,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord: ...

    async def expire_pending(self, *, now: datetime) -> list[ApprovalRecord]: ...


class ApprovalOutcomeHandler(Protocol):
    """Resume or unwind deferred actions after an approval transition."""

    async def on_approved(
        self,
        approval: ApprovalRecord,
    ) -> ApprovalExecutionOutcome: ...

    async def on_rejected(self, approval: ApprovalRecord) -> None: ...

    async def on_expired(self, approval: ApprovalRecord) -> None: ...

    async def on_cancelled(self, approval: ApprovalRecord) -> None: ...


__all__ = ["ApprovalOutcomeHandler", "ApprovalStore"]
