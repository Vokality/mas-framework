"""Facade over desired-state persistence and config run services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ConfigApplyResult

from .desired_state import DesiredStateService
from .runs import ConfigRunService
from .types import ConfigRunHistory, DesiredStateInput, DesiredStateVersionError


class ConfigService:
    """Facade for desired-state persistence plus validation/apply request creation."""

    def __init__(
        self,
        *,
        desired_state_service: DesiredStateService,
        run_service: ConfigRunService,
    ) -> None:
        self._desired_state_service = desired_state_service
        self._run_service = run_service

    async def get_desired_state(
        self,
        session: AsyncSession,
        client_id: str,
    ):
        """Return the current desired-state document for a client."""

        return await self._desired_state_service.get_desired_state(session, client_id)

    async def replace_desired_state(
        self,
        session: AsyncSession,
        payload: DesiredStateInput,
        *,
        actor_user_id: str,
    ):
        """Replace the full desired-state document."""

        return await self._desired_state_service.replace_desired_state(
            session,
            payload,
            actor_user_id=actor_user_id,
        )

    async def create_validation_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        requested_by_user_id: str,
    ):
        """Create a validation request against the latest desired state."""

        return await self._run_service.create_validation_run(
            session,
            client_id=client_id,
            requested_by_user_id=requested_by_user_id,
        )

    async def create_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        requested_by_user_id: str,
    ):
        """Create a pending apply run against the latest desired state."""

        return await self._run_service.create_apply_run(
            session,
            client_id=client_id,
            requested_by_user_id=requested_by_user_id,
        )

    async def list_runs(
        self,
        session: AsyncSession,
        *,
        client_id: str,
    ) -> ConfigRunHistory:
        """Return config validation/apply history for one client."""

        return await self._run_service.list_runs(session, client_id=client_id)

    async def get_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        config_apply_run_id: str,
    ):
        """Return one apply run visible to the target client."""

        return await self._run_service.get_apply_run(
            session,
            client_id=client_id,
            config_apply_run_id=config_apply_run_id,
        )

    async def cancel_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        config_apply_run_id: str,
        actor_user_id: str,
        reason: str,
    ) -> ConfigApplyResult:
        """Cancel one locally owned apply run."""

        return await self._run_service.cancel_apply_run(
            session,
            client_id=client_id,
            config_apply_run_id=config_apply_run_id,
            actor_user_id=actor_user_id,
            reason=reason,
        )


__all__ = [
    "ConfigService",
    "DesiredStateInput",
    "DesiredStateVersionError",
]
