"""Desired-state config routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import (
    ApprovalState,
    ConfigApplyState,
    ConfigApplyResult,
    ConfigDesiredState,
    ConfigValidationResult,
)
from mas_msp_core import ApprovalCancellation

from mas_ops_api.api.dependencies import (
    get_command_connector_registry,
    get_config_service,
    require_client_access,
)
from mas_ops_api.api.schemas import (
    ConfigApplyRequestResponse,
    ConfigApplyRunResponse,
    ConfigRunCancelRequest,
    ConfigApplyStepResponse,
    ConfigRunHistoryResponse,
    ConfigValidationRunResponse,
)
from mas_ops_api.auth.dependencies import get_db_session, require_roles
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ApprovalRequestRecord
from mas_ops_api.config.service import (
    ConfigService,
    DesiredStateInput,
    DesiredStateVersionError,
)


router = APIRouter(prefix="/clients/{client_id}/config", tags=["config"])


@router.get("/desired-state", response_model=ConfigDesiredState)
async def get_desired_state(
    client_id: str = Depends(require_client_access),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
) -> ConfigDesiredState:
    """Fetch the current desired-state config for one client."""

    desired_state = await config_service.get_desired_state(session, client_id)
    if desired_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return ConfigDesiredState.model_validate(desired_state, from_attributes=True)


@router.put("/desired-state", response_model=ConfigDesiredState)
async def replace_desired_state(
    payload: ConfigDesiredState,
    client_id: str = Depends(require_client_access),
    current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
) -> ConfigDesiredState:
    """Replace the desired-state document for one client."""

    if payload.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="client_id_mismatch",
        )
    try:
        record = await config_service.replace_desired_state(
            session,
            DesiredStateInput(
                client_id=payload.client_id,
                fabric_id=payload.fabric_id,
                desired_state_version=payload.desired_state_version,
                tenant_metadata=payload.tenant_metadata,
                policy=payload.policy,
                inventory_sources=payload.inventory_sources,
                notification_routes=payload.notification_routes,
            ),
            actor_user_id=current_user.user_id,
        )
    except DesiredStateVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return ConfigDesiredState.model_validate(record, from_attributes=True)


@router.post("/validate", response_model=ConfigValidationResult)
async def validate_desired_state(
    client_id: str = Depends(require_client_access),
    current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
    command_connector_registry=Depends(get_command_connector_registry),
) -> ConfigValidationResult:
    """Start a validation run for the latest desired state."""

    try:
        run = await config_service.create_validation_run(
            session,
            client_id=client_id,
            requested_by_user_id=current_user.user_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        ) from exc
    return await command_connector_registry.get(client_id).request_config_validation(
        client_id=client_id,
        config_apply_run_id=run.config_apply_run_id,
    )


@router.post("/apply", response_model=ConfigApplyResult)
async def apply_desired_state(
    client_id: str = Depends(require_client_access),
    current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
    command_connector_registry=Depends(get_command_connector_registry),
) -> ConfigApplyResult:
    """Start an apply run for the latest desired state."""

    try:
        run = await config_service.create_apply_run(
            session,
            client_id=client_id,
            requested_by_user_id=current_user.user_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        ) from exc
    await command_connector_registry.get(client_id).request_config_apply(
        client_id=client_id,
        config_apply_run_id=run.config_apply_run_id,
    )
    return ConfigApplyResult(
        config_apply_run_id=run.config_apply_run_id,
        client_id=run.client_id,
        desired_state_version=run.desired_state_version,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_summary=run.error_summary,
    )


@router.get("/runs", response_model=ConfigRunHistoryResponse)
async def list_config_runs(
    client_id: str = Depends(require_client_access),
    _current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
) -> ConfigRunHistoryResponse:
    """Return grouped config validation/apply history for one client."""

    history = await config_service.list_runs(session, client_id=client_id)
    return ConfigRunHistoryResponse(
        validation_runs=[
            ConfigValidationRunResponse.model_validate(row)
            for row in history.validation_runs
        ],
        apply_runs=[
            ConfigApplyRunResponse(
                **ConfigApplyRequestResponse.model_validate(row).model_dump(),
                steps=[
                    ConfigApplyStepResponse.model_validate(step)
                    for step in history.apply_steps.get(row.config_apply_run_id, [])
                ],
            )
            for row in history.apply_runs
        ],
    )


@router.post("/runs/{config_apply_run_id}/cancel", response_model=ConfigApplyResult)
async def cancel_config_apply_run(
    config_apply_run_id: str,
    payload: ConfigRunCancelRequest,
    client_id: str = Depends(require_client_access),
    current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
    command_connector_registry=Depends(get_command_connector_registry),
) -> ConfigApplyResult:
    """Cancel one pending or validating apply run."""

    run = await config_service.get_apply_run(
        session,
        client_id=client_id,
        config_apply_run_id=config_apply_run_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    run_state = ConfigApplyState(run.status)
    if run_state not in {ConfigApplyState.PENDING, ConfigApplyState.VALIDATING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="not_cancellable"
        )

    active_approval = None
    if run.approval_id is not None:
        active_approval = await session.get(ApprovalRequestRecord, run.approval_id)
    if active_approval is not None and ApprovalState(active_approval.state) in {
        ApprovalState.PENDING,
        ApprovalState.APPROVED,
    }:
        await command_connector_registry.get(client_id).dispatch_approval_cancellation(
            cancellation=ApprovalCancellation(
                approval_id=active_approval.approval_id,
                cancelled_at=utc_now(),
                actor_type="user",
                actor_id=current_user.user_id,
                reason=payload.reason or "config_apply_cancelled",
            )
        )
        await session.refresh(run)
        return ConfigApplyResult(
            config_apply_run_id=run.config_apply_run_id,
            client_id=run.client_id,
            desired_state_version=run.desired_state_version,
            status=ConfigApplyState(run.status),
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_summary=run.error_summary,
        )

    return await config_service.cancel_apply_run(
        session,
        client_id=client_id,
        config_apply_run_id=config_apply_run_id,
        actor_user_id=current_user.user_id,
        reason=payload.reason or "config_apply_cancelled",
    )


__all__ = ["router"]
