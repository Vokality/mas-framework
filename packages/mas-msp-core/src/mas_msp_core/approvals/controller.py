"""Phase-4 approval controller."""

from __future__ import annotations

from datetime import datetime

from mas_msp_contracts import ApprovalDecision, ApprovalRequested, ApprovalState

from .models import ApprovalCancellation, ApprovalRecord
from .ports import ApprovalOutcomeHandler, ApprovalStore


class ApprovalController:
    """Own approval records, decisions, expiry handling, and action resume."""

    def __init__(
        self,
        *,
        store: ApprovalStore,
        outcome_handler: ApprovalOutcomeHandler,
    ) -> None:
        self._store = store
        self._outcome_handler = outcome_handler

    async def request_approval(
        self,
        approval_request: ApprovalRequested,
    ) -> ApprovalRecord:
        """Persist a new pending approval request."""

        return await self._store.create_request(approval_request)

    async def apply_decision(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalRecord:
        """Apply one human approval decision and resume the deferred action."""

        approval = await self._store.resolve_request(decision)
        if approval.state is ApprovalState.APPROVED:
            if await self._outcome_handler.on_approved(approval):
                return await self._store.mark_executed(
                    approval.approval_id,
                    executed_at=approval.approved_at or decision.decided_at,
                )
            return approval
        await self._outcome_handler.on_rejected(approval)
        return approval

    async def expire_pending(self, *, now: datetime) -> list[ApprovalRecord]:
        """Expire pending approvals whose deadline has elapsed."""

        expired = await self._store.expire_pending(now=now)
        for approval in expired:
            await self._outcome_handler.on_expired(approval)
        return expired

    async def cancel_request(
        self,
        cancellation: ApprovalCancellation,
    ) -> ApprovalRecord:
        """Cancel a pending or approved request before execution begins."""

        approval = await self._store.cancel_request(cancellation)
        await self._outcome_handler.on_cancelled(approval)
        return approval


__all__ = ["ApprovalController"]
