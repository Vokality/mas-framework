"""Explicit client enrollment for ops-plane managed tenants."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.audit import AuditEntryInput, AuditService
from mas_ops_api.config.defaults import build_initial_desired_state_record
from mas_ops_api.db.base import utc_now
from mas_ops_api.db.models import ConfigDesiredStateRecord, PortfolioClient
from mas_ops_api.projections.stream_payloads import serialize_client_summary
from mas_ops_api.streams.service import StreamService


@dataclass(frozen=True, slots=True)
class ClientEnrollmentInput:
    """Inputs required to enroll one managed client."""

    client_id: str
    fabric_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ClientEnrollmentResult:
    """Outcome of one idempotent client enrollment operation."""

    client: PortfolioClient
    desired_state: ConfigDesiredStateRecord
    client_created: bool
    desired_state_created: bool


class ClientEnrollmentService:
    """Create the minimum durable records required for a managed client."""

    def __init__(
        self,
        *,
        audit_service: AuditService,
        stream_service: StreamService,
    ) -> None:
        self._audit_service = audit_service
        self._stream_service = stream_service

    async def ensure_enrolled(
        self,
        session: AsyncSession,
        payload: ClientEnrollmentInput,
        *,
        actor_type: str,
        actor_id: str,
    ) -> ClientEnrollmentResult:
        """Ensure the client summary and desired-state document both exist."""

        now = utc_now()
        client = await session.get(PortfolioClient, payload.client_id)
        desired_state = await session.get(ConfigDesiredStateRecord, payload.client_id)

        if client is not None and client.fabric_id != payload.fabric_id:
            raise ValueError("client already exists with a different fabric_id")
        if desired_state is not None and desired_state.fabric_id != payload.fabric_id:
            raise ValueError("desired-state already exists with a different fabric_id")

        client_created = False
        client_changed = False
        desired_state_created = False
        stream_events = []

        resolved_name = _resolve_client_name(desired_state, payload.display_name)
        if client is None:
            client = PortfolioClient(
                client_id=payload.client_id,
                fabric_id=payload.fabric_id,
                name=resolved_name,
                open_alert_count=0,
                critical_asset_count=0,
                updated_at=now,
            )
            session.add(client)
            client_created = True
        else:
            if client.name != resolved_name:
                client.name = resolved_name
                client.updated_at = max(client.updated_at, now)
                client_changed = True

        if desired_state is None:
            desired_state = build_initial_desired_state_record(
                client_id=payload.client_id,
                fabric_id=payload.fabric_id,
                display_name=resolved_name,
                updated_at=now,
            )
            session.add(desired_state)
            desired_state_created = True

        await session.flush()

        if client_created or client_changed:
            stream_events.append(
                self._stream_service.build_event(
                    event_name="client.updated",
                    client_id=client.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="portfolio_client",
                    subject_id=client.client_id,
                    payload=serialize_client_summary(client),
                    occurred_at=client.updated_at,
                )
            )
        if desired_state_created:
            stream_events.append(
                self._stream_service.build_event(
                    event_name="client.updated",
                    client_id=desired_state.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="config_desired_state",
                    subject_id=desired_state.client_id,
                    payload={
                        "client_id": desired_state.client_id,
                        "fabric_id": desired_state.fabric_id,
                        "desired_state_version": desired_state.desired_state_version,
                    },
                    occurred_at=desired_state.updated_at,
                )
            )

        if stream_events:
            session.add_all(stream_events)
            await self._audit_service.append_entry(
                session,
                AuditEntryInput(
                    client_id=payload.client_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    target_type="portfolio_client",
                    target_id=payload.client_id,
                    action="client.enrolled",
                    outcome="succeeded",
                    details={
                        "fabric_id": payload.fabric_id,
                        "desired_state_created": desired_state_created,
                    },
                    occurred_at=now,
                ),
            )
            await session.commit()
            await session.refresh(client)
            await session.refresh(desired_state)
            for stream_event in stream_events:
                await self._stream_service.publish(stream_event)
        return ClientEnrollmentResult(
            client=client,
            desired_state=desired_state,
            client_created=client_created,
            desired_state_created=desired_state_created,
        )


def _resolve_client_name(
    desired_state: ConfigDesiredStateRecord | None,
    requested_display_name: str,
) -> str:
    if desired_state is None:
        return requested_display_name
    display_name = desired_state.tenant_metadata.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    return requested_display_name


__all__ = [
    "ClientEnrollmentInput",
    "ClientEnrollmentResult",
    "ClientEnrollmentService",
]
