"""Ops-plane incident read/write adapters used by the local fabric runtime."""

from __future__ import annotations

from datetime import datetime

from mas_msp_contracts import (
    AssetKind,
    AssetRef,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    JSONObject,
    Severity,
)
from mas_msp_core import IncidentContextReader, NotifierTransport

from mas_ops_api.db.session import Database
from mas_ops_api.incidents.service import IncidentProjectionService
from mas_ops_api.projections.repository import PortfolioQueries
from mas_ops_api.streams.service import StreamService


class OpsPlaneIncidentGateway(IncidentContextReader, NotifierTransport):
    """Adapt ops-plane persistence to the fabric-local phase-3 ports."""

    def __init__(
        self,
        *,
        database: Database,
        incident_projection_service: IncidentProjectionService,
        stream_service: StreamService,
    ) -> None:
        self._database = database
        self._incident_projection_service = incident_projection_service
        self._stream_service = stream_service

    async def find_active_incident_for_asset(
        self,
        *,
        client_id: str,
        asset_id: str,
    ) -> IncidentRecord | None:
        async with self._database.session_factory() as session:
            incident = await PortfolioQueries.find_active_incident_for_asset(
                session,
                client_id=client_id,
                asset_id=asset_id,
            )
            if incident is None:
                return None
            assets = await PortfolioQueries.list_assets_for_incident(
                session, incident.incident_id
            )
            return _incident_record(incident, assets)

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        async with self._database.session_factory() as session:
            incident = await PortfolioQueries.get_incident(session, incident_id)
            if incident is None:
                return None
            assets = await PortfolioQueries.list_assets_for_incident(
                session, incident_id
            )
            return _incident_record(incident, assets)

    async def list_assets_for_incident(self, incident_id: str) -> list[AssetRef]:
        async with self._database.session_factory() as session:
            assets = await PortfolioQueries.list_assets_for_incident(session, incident_id)
            return [_asset_ref(asset) for asset in assets]

    async def list_activity_for_incident(
        self,
        incident_id: str,
    ) -> list[JSONObject]:
        async with self._database.session_factory() as session:
            rows = await PortfolioQueries.list_activity_for_incident(session, incident_id)
            return [
                {
                    "activity_id": row.activity_id,
                    "event_type": row.event_type,
                    "payload": row.payload,
                    "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                }
                for row in rows
            ]

    async def list_evidence_for_incident(
        self,
        incident_id: str,
    ) -> list[EvidenceBundle]:
        async with self._database.session_factory() as session:
            rows = await PortfolioQueries.list_evidence_for_incident(session, incident_id)
            return [
                EvidenceBundle(
                    evidence_bundle_id=row.evidence_bundle_id,
                    incident_id=row.incident_id,
                    asset_id=row.asset_id,
                    collected_at=row.collected_at,
                    items=list(row.items),
                    summary=row.summary,
                )
                for row in rows
            ]

    async def record_visibility_incident(
        self,
        *,
        incident_id: str | None,
        client_id: str,
        fabric_id: str,
        summary: str,
        severity: Severity,
        state: IncidentState,
        asset_ids: list[str],
        occurred_at: datetime,
        source: str,
        source_event_id: str,
    ) -> IncidentRecord:
        if incident_id is None:
            raise ValueError("visibility incidents require a fabric-owned incident_id")
        stream_events = []
        async with self._database.session_factory() as session:
            existing = await PortfolioQueries.get_incident(session, incident_id)
            if existing is None:
                await self._incident_projection_service.create_incident(
                    session,
                    incident_id=incident_id,
                    client_id=client_id,
                    fabric_id=fabric_id,
                    state=state,
                    severity=severity.value,
                    summary=summary,
                    recommended_actions=[],
                    asset_ids=asset_ids,
                    opened_at=occurred_at,
                )
            incident, stream_events = await self._incident_projection_service.persist_summary(
                session,
                incident_id=incident_id,
                summary=summary,
                severity=severity.value,
                state=state,
                recommended_actions=None,
                asset_ids=asset_ids,
                occurred_at=occurred_at,
                activity_payload={
                    "summary": summary,
                    "severity": severity.value,
                    "state": state.value,
                    "source": source,
                },
                activity_source_event_id=source_event_id,
            )
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
            assets = await PortfolioQueries.list_assets_for_incident(
                session, incident.incident_id
            )
            result = _incident_record(incident, assets)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return result

    async def persist_summary(
        self,
        *,
        incident_id: str,
        summary: str,
        severity: Severity,
        state: IncidentState,
        recommended_actions: list[JSONObject] | None,
        asset_ids: list[str],
    ) -> IncidentRecord:
        stream_events = []
        async with self._database.session_factory() as session:
            incident, stream_events = await self._incident_projection_service.persist_summary(
                session,
                incident_id=incident_id,
                summary=summary,
                severity=severity.value,
                state=state,
                recommended_actions=(
                    None
                    if recommended_actions is None
                    else [dict(action) for action in recommended_actions]
                ),
                asset_ids=asset_ids,
            )
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
            assets = await PortfolioQueries.list_assets_for_incident(
                session, incident.incident_id
            )
            result = _incident_record(incident, assets)
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return result

    async def append_activity(
        self,
        *,
        incident_id: str,
        event_type: str,
        payload: JSONObject,
        asset_id: str | None,
        occurred_at: datetime | None = None,
    ) -> None:
        stream_events = []
        async with self._database.session_factory() as session:
            _activity, stream_events = await self._incident_projection_service.append_activity(
                session,
                incident_id=incident_id,
                event_type=event_type,
                payload=payload,
                asset_id=asset_id,
                occurred_at=occurred_at,
            )
            session.add_all(stream_events)
            await session.flush()
            await session.commit()
        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)

    async def persist_evidence_bundle(
        self,
        *,
        bundle: EvidenceBundle,
        client_id: str,
        fabric_id: str,
    ) -> None:
        async with self._database.session_factory() as session:
            await self._incident_projection_service.persist_evidence_bundle(
                session,
                evidence_bundle_id=bundle.evidence_bundle_id,
                incident_id=bundle.incident_id,
                asset_id=bundle.asset_id,
                client_id=client_id,
                fabric_id=fabric_id,
                collected_at=bundle.collected_at,
                summary=bundle.summary,
                items=[dict(item) for item in bundle.items],
            )
            await session.commit()


def _asset_ref(asset) -> AssetRef:  # noqa: ANN001
    return AssetRef(
        asset_id=asset.asset_id,
        client_id=asset.client_id,
        fabric_id=asset.fabric_id,
        asset_kind=AssetKind(asset.asset_kind),
        vendor=asset.vendor,
        model=asset.model,
        hostname=asset.hostname,
        mgmt_address=asset.mgmt_address,
        site=asset.site,
        tags=list(asset.tags),
    )


def _incident_record(incident, assets) -> IncidentRecord:  # noqa: ANN001
    return IncidentRecord(
        incident_id=incident.incident_id,
        client_id=incident.client_id,
        fabric_id=incident.fabric_id,
        state=IncidentState(incident.state),
        severity=Severity(incident.severity),
        summary=incident.summary,
        asset_ids=[asset.asset_id for asset in assets],
        opened_at=incident.opened_at,
        updated_at=incident.updated_at,
    )


__all__ = ["OpsPlaneIncidentGateway"]
