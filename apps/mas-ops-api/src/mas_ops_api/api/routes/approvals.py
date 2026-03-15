"""Approval routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ApprovalDecision, ApprovalDecisionValue, ApprovalState

from mas_ops_api.api.dependencies import (
    get_command_connector_registry,
    load_approval_for_user,
)
from mas_ops_api.api.schemas import ApprovalDecisionRequest, ApprovalResponse
from mas_ops_api.auth.dependencies import (
    get_current_user,
    get_db_session,
    require_roles,
)
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ApprovalRequestRecord


router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ApprovalResponse]:
    """List approvals visible to the current user."""

    stmt = select(ApprovalRequestRecord).order_by(
        ApprovalRequestRecord.requested_at.desc()
    )
    if current_user.role is not UserRole.ADMIN:
        stmt = stmt.where(
            ApprovalRequestRecord.client_id.in_(current_user.allowed_client_ids)
        )
    rows = (await session.scalars(stmt)).all()
    return [ApprovalResponse.model_validate(row) for row in rows]


@router.post("/{approval_id}/decision", response_model=ApprovalResponse)
async def submit_approval_decision(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    current_user: AuthenticatedUser = Depends(
        require_roles(UserRole.ADMIN, UserRole.OPERATOR)
    ),
    session: AsyncSession = Depends(get_db_session),
    command_connector_registry=Depends(get_command_connector_registry),
) -> ApprovalResponse:
    """Submit a human approval decision for a pending approval."""

    approval = await load_approval_for_user(
        approval_id,
        session=session,
        user=current_user,
    )
    if approval.state != ApprovalState.PENDING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_pending")

    try:
        await command_connector_registry.get(
            approval.client_id
        ).dispatch_approval_decision(
            decision=ApprovalDecision(
                approval_id=approval.approval_id,
                decided_by_user_id=current_user.user_id,
                decision=ApprovalDecisionValue(payload.decision),
                decided_at=utc_now(),
                reason=payload.reason,
            )
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await session.refresh(approval)
    return ApprovalResponse.model_validate(approval)


__all__ = ["router"]
