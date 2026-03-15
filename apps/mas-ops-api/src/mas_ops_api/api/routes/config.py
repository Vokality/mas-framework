"""Desired-state config routes for the ops-plane API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import (
    ConfigApplyResult,
    ConfigDesiredState,
    ConfigValidationResult,
)

from mas_ops_api.api.dependencies import (
    get_config_service,
    get_command_connector_registry,
    require_client_access,
)
from mas_ops_api.auth.dependencies import get_db_session, require_roles
from mas_ops_api.auth.types import AuthenticatedUser, UserRole
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
    _current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
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
        )
    except DesiredStateVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return ConfigDesiredState.model_validate(record, from_attributes=True)


@router.post("/validate", response_model=ConfigValidationResult)
async def validate_desired_state(
    client_id: str = Depends(require_client_access),
    _current_user: AuthenticatedUser = Depends(require_roles(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
    config_service: ConfigService = Depends(get_config_service),
    command_connector_registry=Depends(get_command_connector_registry),
) -> ConfigValidationResult:
    """Start a validation run for the latest desired state."""

    try:
        run = await config_service.create_validation_run(session, client_id=client_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        ) from exc
    await command_connector_registry.get(client_id).request_config_validation(
        client_id=client_id,
        config_apply_run_id=run.config_apply_run_id,
    )
    return ConfigValidationResult.model_validate(run, from_attributes=True)


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
        status=run.status,  # type: ignore[arg-type]
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_summary=run.error_summary,
    )


__all__ = ["router"]
