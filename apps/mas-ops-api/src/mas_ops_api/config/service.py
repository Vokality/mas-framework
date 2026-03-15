"""Desired-state persistence and run tracking services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ConfigApplyState

from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import (
    ConfigApplyRun,
    ConfigDesiredStateRecord,
    ConfigValidationRun,
)
from mas_ops_api.streams.service import StreamService


class DesiredStateVersionError(ValueError):
    """Raised when a config replacement does not satisfy optimistic versioning."""


@dataclass(frozen=True, slots=True)
class DesiredStateInput:
    """Input payload for desired-state replacement."""

    client_id: str
    fabric_id: str
    desired_state_version: int
    tenant_metadata: dict[str, object]
    policy: dict[str, object]
    inventory_sources: list[dict[str, object]]
    notification_routes: list[dict[str, object]]


class ConfigService:
    """Persist desired-state config and create validation/apply runs."""

    def __init__(self, stream_service: StreamService) -> None:
        self._stream_service = stream_service

    async def get_desired_state(
        self,
        session: AsyncSession,
        client_id: str,
    ) -> ConfigDesiredStateRecord | None:
        """Return the current desired-state document for a client."""

        return await session.get(ConfigDesiredStateRecord, client_id)

    async def replace_desired_state(
        self,
        session: AsyncSession,
        payload: DesiredStateInput,
    ) -> ConfigDesiredStateRecord:
        """Replace the current desired-state document with optimistic versioning."""

        existing = await session.get(ConfigDesiredStateRecord, payload.client_id)
        expected_version = 1 if existing is None else existing.desired_state_version + 1
        if payload.desired_state_version != expected_version:
            raise DesiredStateVersionError(
                f"expected desired_state_version={expected_version}"
            )

        now = utc_now()
        if existing is None:
            record = ConfigDesiredStateRecord(
                client_id=payload.client_id,
                fabric_id=payload.fabric_id,
                desired_state_version=payload.desired_state_version,
                tenant_metadata=payload.tenant_metadata,
                policy=payload.policy,
                inventory_sources=payload.inventory_sources,
                notification_routes=payload.notification_routes,
                updated_at=now,
            )
            session.add(record)
        else:
            existing.fabric_id = payload.fabric_id
            existing.desired_state_version = payload.desired_state_version
            existing.tenant_metadata = payload.tenant_metadata
            existing.policy = payload.policy
            existing.inventory_sources = payload.inventory_sources
            existing.notification_routes = payload.notification_routes
            existing.updated_at = now
            record = existing

        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=payload.client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_desired_state",
            subject_id=payload.client_id,
            payload={
                "client_id": payload.client_id,
                "desired_state_version": payload.desired_state_version,
            },
            occurred_at=now,
        )
        session.add(stream_event)
        await session.commit()
        await session.refresh(record)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return record

    async def create_validation_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
    ) -> ConfigValidationRun:
        """Create a deterministic validation result for the latest desired state."""

        desired_state = await session.get(ConfigDesiredStateRecord, client_id)
        if desired_state is None:
            raise LookupError("desired-state config not found")

        run = ConfigValidationRun(
            config_apply_run_id=str(uuid4()),
            client_id=client_id,
            desired_state_version=desired_state.desired_state_version,
            status="valid",
            errors=[],
            warnings=[],
            validated_at=utc_now(),
        )
        session.add(run)
        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_validation_run",
            subject_id=run.config_apply_run_id,
            payload={
                "config_apply_run_id": run.config_apply_run_id,
                "status": run.status,
                "desired_state_version": run.desired_state_version,
            },
            occurred_at=run.validated_at,
        )
        session.add(stream_event)
        await session.commit()
        await session.refresh(run)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return run

    async def create_apply_run(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        requested_by_user_id: str,
    ) -> ConfigApplyRun:
        """Create a pending apply run against the latest desired state version."""

        desired_state = await session.get(ConfigDesiredStateRecord, client_id)
        if desired_state is None:
            raise LookupError("desired-state config not found")

        run = ConfigApplyRun(
            config_apply_run_id=str(uuid4()),
            client_id=client_id,
            desired_state_version=desired_state.desired_state_version,
            status=ConfigApplyState.PENDING.value,
            requested_by_user_id=requested_by_user_id,
            requested_at=utc_now(),
        )
        session.add(run)
        stream_event = self._stream_service.build_event(
            event_name="client.updated",
            client_id=client_id,
            incident_id=None,
            chat_session_id=None,
            subject_type="config_apply_run",
            subject_id=run.config_apply_run_id,
            payload={
                "config_apply_run_id": run.config_apply_run_id,
                "status": run.status,
                "desired_state_version": run.desired_state_version,
            },
            occurred_at=run.requested_at,
        )
        session.add(stream_event)
        await session.commit()
        await session.refresh(run)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return run


__all__ = [
    "ConfigService",
    "DesiredStateInput",
    "DesiredStateVersionError",
]
