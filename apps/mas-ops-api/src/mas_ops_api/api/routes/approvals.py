"""Approval routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ApprovalDecisionValue, ApprovalState

from mas_ops_api.api.dependencies import get_stream_service, load_approval_for_user
from mas_ops_api.api.schemas import ApprovalDecisionRequest, ApprovalResponse
from mas_ops_api.auth.dependencies import (
    get_current_user,
    get_db_session,
    require_roles,
)
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ApprovalRequestRecord
from mas_ops_api.streams.service import StreamService


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
    stream_service: StreamService = Depends(get_stream_service),
) -> ApprovalResponse:
    """Submit a human approval decision for a pending approval."""

    approval = await load_approval_for_user(
        approval_id,
        session=session,
        user=current_user,
    )
    if approval.state != ApprovalState.PENDING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_pending")

    now = utc_now()
    approval.decided_by_user_id = current_user.user_id
    approval.decision_reason = payload.reason
    approval.decided_at = now
    if payload.decision is ApprovalDecisionValue.APPROVE:
        approval.state = ApprovalState.APPROVED.value
        approval.approved_at = now
    else:
        approval.state = ApprovalState.REJECTED.value
        approval.rejected_at = now

    stream_event = stream_service.build_event(
        event_name="approval.resolved",
        client_id=approval.client_id,
        incident_id=approval.incident_id,
        chat_session_id=None,
        subject_type="approval_request",
        subject_id=approval.approval_id,
        payload={
            "approval_id": approval.approval_id,
            "state": approval.state,
            "decision": payload.decision.value,
        },
        occurred_at=now,
    )
    session.add(stream_event)
    await session.commit()
    await session.refresh(approval)
    await session.refresh(stream_event)
    await stream_service.publish(stream_event)
    return ApprovalResponse.model_validate(approval)


__all__ = ["router"]
