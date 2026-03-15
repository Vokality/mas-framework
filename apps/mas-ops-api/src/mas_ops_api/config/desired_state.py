"""Desired-state persistence and validation helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import ConfigDesiredState
from mas_msp_core.config import DesiredStateStore

from mas_ops_api.audit import AuditEntryInput, AuditService
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ConfigDesiredStateRecord
from mas_ops_api.db.session import Database
from mas_ops_api.streams.service import StreamService

from .types import DesiredStateInput, DesiredStateVersionError


SECRET_VALUE_KEYS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "secret_value",
        "token",
    }
)


class DesiredStateService:
    """Persist full desired-state replacements with optimistic versioning."""

    def __init__(
        self,
        *,
        audit_service: AuditService,
        stream_service: StreamService,
    ) -> None:
        self._audit_service = audit_service
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
        *,
        actor_user_id: str,
    ) -> ConfigDesiredStateRecord:
        """Replace the current desired-state document with optimistic versioning."""

        existing = await session.get(ConfigDesiredStateRecord, payload.client_id)
        expected_version = 1 if existing is None else existing.desired_state_version + 1
        if payload.desired_state_version != expected_version:
            raise DesiredStateVersionError(
                f"expected desired_state_version={expected_version}"
            )
        _ensure_no_secret_value_keys("tenant_metadata", payload.tenant_metadata)
        _ensure_no_secret_value_keys("policy", payload.policy)
        _ensure_no_secret_value_keys("inventory_sources", payload.inventory_sources)
        _ensure_no_secret_value_keys("notification_routes", payload.notification_routes)

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
        await self._audit_service.append_entry(
            session,
            AuditEntryInput(
                client_id=payload.client_id,
                actor_type="user",
                actor_id=actor_user_id,
                target_type="config_desired_state",
                target_id=payload.client_id,
                action="config.desired_state.replaced",
                outcome="succeeded",
                details={"desired_state_version": payload.desired_state_version},
                occurred_at=now,
            ),
        )
        await session.commit()
        await session.refresh(record)
        await session.refresh(stream_event)
        await self._stream_service.publish(stream_event)
        return record


class OpsPlaneDesiredStateStore(DesiredStateStore):
    """Database-backed desired-state loader for the config deployer."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def get_desired_state(self, client_id: str) -> ConfigDesiredState | None:
        async with self._database.session_factory() as session:
            row = await session.get(ConfigDesiredStateRecord, client_id)
            if row is None:
                return None
            return ConfigDesiredState.model_validate(row, from_attributes=True)


def _ensure_no_secret_value_keys(path: str, value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SECRET_VALUE_KEYS:
                raise ValueError(f"{path}.{key} may contain only secret references")
            _ensure_no_secret_value_keys(f"{path}.{key}", nested)
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _ensure_no_secret_value_keys(f"{path}[{index}]", nested)


__all__ = ["DesiredStateService", "OpsPlaneDesiredStateStore", "SECRET_VALUE_KEYS"]
