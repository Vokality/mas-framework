"""Incident projection and evidence persistence primitives for the ops plane."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mas_msp_contracts import INCIDENT_STATE_MACHINE, IncidentState

from mas_ops_api.db.models import (
    IncidentEvidenceBundleRecord,
    OpsStreamEvent,
    PortfolioActivityEvent,
    PortfolioIncident,
    PortfolioIncidentAsset,
)
from mas_ops_api.projections.stream_payloads import (
    serialize_activity,
    serialize_incident,
)
from mas_ops_api.streams.service import StreamService


class IncidentProjectionService:
    """Persist incidents, evidence bundles, and activity timeline entries."""

    def __init__(self, stream_service: StreamService) -> None:
        self._stream_service = stream_service

    async def create_incident(
        self,
        session: AsyncSession,
        *,
        incident_id: str,
        client_id: str,
        fabric_id: str,
        correlation_key: str | None,
        state: IncidentState,
        severity: str,
        summary: str,
        recommended_actions: list[dict[str, object]],
        asset_ids: list[str],
        opened_at: datetime,
    ) -> PortfolioIncident:
        """Create one incident record and attach the referenced assets."""

        incident = PortfolioIncident(
            incident_id=incident_id,
            client_id=client_id,
            fabric_id=fabric_id,
            correlation_key=correlation_key,
            state=state.value,
            severity=severity,
            summary=summary,
            recommended_actions=list(recommended_actions),
            opened_at=opened_at.astimezone(UTC),
            updated_at=opened_at.astimezone(UTC),
        )
        session.add(incident)
        await session.flush()
        await self._sync_asset_links(
            session,
            incident_id=incident_id,
            asset_ids=asset_ids,
        )
        await session.flush()
        return incident

    async def append_activity(
        self,
        session: AsyncSession,
        *,
        incident_id: str,
        event_type: str,
        payload: dict[str, object],
        asset_id: str | None,
        occurred_at: datetime | None = None,
        source_event_id: str | None = None,
    ) -> tuple[PortfolioActivityEvent, list[OpsStreamEvent]]:
        """Append one incident activity entry and build the matching stream event."""

        incident = await self._require_incident(session, incident_id)
        entry = PortfolioActivityEvent(
            source_event_id=source_event_id or str(uuid4()),
            client_id=incident.client_id,
            fabric_id=incident.fabric_id,
            incident_id=incident.incident_id,
            asset_id=asset_id,
            event_type=event_type,
            subject_type="incident",
            subject_id=incident.incident_id,
            payload=payload,
            occurred_at=(occurred_at or datetime.now(UTC)).astimezone(UTC),
        )
        session.add(entry)
        await session.flush()
        return entry, [
            self._stream_service.build_event(
                event_name="activity.appended",
                client_id=entry.client_id,
                incident_id=entry.incident_id,
                chat_session_id=None,
                subject_type="portfolio_activity",
                subject_id=str(entry.activity_id),
                payload=serialize_activity(entry),
                occurred_at=entry.occurred_at,
            )
        ]

    async def persist_summary(
        self,
        session: AsyncSession,
        *,
        incident_id: str,
        summary: str,
        correlation_key: str | None = None,
        severity: str,
        state: IncidentState,
        recommended_actions: list[dict[str, object]] | None,
        asset_ids: list[str],
        occurred_at: datetime | None = None,
        activity_payload: dict[str, object] | None = None,
        activity_source_event_id: str | None = None,
    ) -> tuple[PortfolioIncident, list[OpsStreamEvent]]:
        """Persist the latest incident summary and build the matching stream events."""

        incident = await self._require_incident(session, incident_id)
        current_state = IncidentState(incident.state)
        if current_state is not state:
            INCIDENT_STATE_MACHINE.require_transition(current_state, state)
            incident.state = state.value
        if correlation_key is not None:
            incident.correlation_key = correlation_key
        incident.summary = summary
        incident.severity = severity
        if recommended_actions is not None:
            incident.recommended_actions = list(recommended_actions)
        incident.updated_at = (occurred_at or datetime.now(UTC)).astimezone(UTC)
        await self._sync_asset_links(
            session,
            incident_id=incident_id,
            asset_ids=asset_ids,
        )
        activity, activity_events = await self.append_activity(
            session,
            incident_id=incident_id,
            event_type="incident.updated",
            payload=activity_payload or serialize_incident(incident),
            asset_id=asset_ids[0] if asset_ids else None,
            occurred_at=incident.updated_at,
            source_event_id=activity_source_event_id,
        )
        del activity
        return incident, [
            self._stream_service.build_event(
                event_name="incident.updated",
                client_id=incident.client_id,
                incident_id=incident.incident_id,
                chat_session_id=None,
                subject_type="incident",
                subject_id=incident.incident_id,
                payload=serialize_incident(incident),
                occurred_at=incident.updated_at,
            ),
            *activity_events,
        ]

    async def persist_evidence_bundle(
        self,
        session: AsyncSession,
        *,
        evidence_bundle_id: str,
        incident_id: str,
        asset_id: str,
        client_id: str,
        fabric_id: str,
        collected_at: datetime,
        summary: str,
        items: list[dict[str, object]],
    ) -> None:
        """Persist one evidence bundle if it does not already exist."""

        existing = await session.get(IncidentEvidenceBundleRecord, evidence_bundle_id)
        if existing is not None:
            return
        session.add(
            IncidentEvidenceBundleRecord(
                evidence_bundle_id=evidence_bundle_id,
                incident_id=incident_id,
                asset_id=asset_id,
                client_id=client_id,
                fabric_id=fabric_id,
                collected_at=collected_at,
                summary=summary,
                items=list(items),
            )
        )

    async def _require_incident(
        self,
        session: AsyncSession,
        incident_id: str,
    ) -> PortfolioIncident:
        incident = await session.get(PortfolioIncident, incident_id)
        if incident is None:
            raise LookupError(f"incident {incident_id!r} was not found")
        return incident

    async def _sync_asset_links(
        self,
        session: AsyncSession,
        *,
        incident_id: str,
        asset_ids: list[str],
    ) -> None:
        rows = list(
            (
                await session.scalars(
                    select(PortfolioIncidentAsset).where(
                        PortfolioIncidentAsset.incident_id == incident_id
                    )
                )
            ).all()
        )
        existing_asset_ids = {row.asset_id for row in rows}
        desired_asset_ids = set(asset_ids)
        for row in rows:
            if row.asset_id not in desired_asset_ids:
                await session.delete(row)
        for asset_id in desired_asset_ids - existing_asset_ids:
            session.add(
                PortfolioIncidentAsset(incident_id=incident_id, asset_id=asset_id)
            )


__all__ = ["IncidentProjectionService"]
