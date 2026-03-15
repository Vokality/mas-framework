"""Projection writer for Phase 2 portfolio visibility events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mas_msp_contracts import (
    AlertRaised,
    AssetRef,
    HealthSnapshot,
    HealthState,
    PortfolioEvent,
)

from mas_ops_api.db.models import (
    ConfigDesiredStateRecord,
    OpsStreamEvent,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
)
from mas_ops_api.db.session import Database
from mas_ops_api.projections.source_ids import build_projection_source_id
from mas_ops_api.projections.stream_payloads import (
    serialize_activity,
    serialize_asset,
    serialize_client_summary,
)
from mas_ops_api.streams.service import StreamService


@dataclass(frozen=True, slots=True)
class ProjectionWriteResult:
    """Return whether a portfolio event changed the read models."""

    applied: bool


class PortfolioIngestService:
    """Consume normalized portfolio events into ops-plane read models."""

    def __init__(
        self,
        database: Database,
        stream_service: StreamService,
    ) -> None:
        self._database = database
        self._stream_service = stream_service

    async def ingest_event(self, event: PortfolioEvent) -> ProjectionWriteResult:
        """Apply one portfolio event idempotently to the read models."""

        projection_source_id = build_projection_source_id(event)
        async with self._database.session_factory() as session:
            if await self._activity_exists(session, event, projection_source_id):
                return ProjectionWriteResult(applied=False)

            client, client_changed = await self._get_or_create_client(session, event)
            asset, asset_changed = await self._apply_asset_projection(session, event)
            activity = PortfolioActivityEvent(
                source_event_id=projection_source_id,
                client_id=event.client_id,
                fabric_id=event.fabric_id,
                incident_id=None,
                asset_id=asset.asset_id if asset is not None else None,
                event_type=event.event_type,
                subject_type=event.subject_type,
                subject_id=event.subject_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )
            session.add(activity)

            if event.event_type in {"network.alert.raised", "host.alert.raised"}:
                client.open_alert_count += 1
                client_changed = True
            if event.occurred_at > client.updated_at:
                client.updated_at = event.occurred_at
                client_changed = True

            await session.flush()
            critical_asset_count = await self._count_critical_assets(
                session, event.client_id
            )
            if critical_asset_count != client.critical_asset_count:
                client.critical_asset_count = critical_asset_count
                client_changed = True
            stream_events = self._build_stream_events(
                client=client if client_changed else None,
                asset=asset if asset_changed else None,
                activity=activity,
                occurred_at=event.occurred_at,
            )
            session.add_all(stream_events)
            await session.flush()

            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if await self._activity_exists(session, event, projection_source_id):
                    return ProjectionWriteResult(applied=False)
                raise

        for stream_event in stream_events:
            await self._stream_service.publish(stream_event)
        return ProjectionWriteResult(applied=True)

    async def _activity_exists(
        self,
        session,  # noqa: ANN001
        event: PortfolioEvent,
        projection_source_id: str,
    ) -> bool:
        existing = await session.scalar(
            select(PortfolioActivityEvent.activity_id).where(
                PortfolioActivityEvent.client_id == event.client_id,
                PortfolioActivityEvent.fabric_id == event.fabric_id,
                PortfolioActivityEvent.source_event_id == projection_source_id,
            )
        )
        return existing is not None

    async def _get_or_create_client(
        self,
        session,  # noqa: ANN001
        event: PortfolioEvent,
    ) -> tuple[PortfolioClient, bool]:
        client = await session.get(PortfolioClient, event.client_id)
        resolved_name = await self._resolve_client_name(
            session,
            client_id=event.client_id,
            existing_name=None if client is None else client.name,
        )
        if client is None:
            client = PortfolioClient(
                client_id=event.client_id,
                fabric_id=event.fabric_id,
                name=resolved_name,
                open_alert_count=0,
                critical_asset_count=0,
                updated_at=event.occurred_at,
            )
            session.add(client)
            return client, True

        changed = False
        if client.name != resolved_name:
            client.name = resolved_name
            changed = True
        if client.fabric_id != event.fabric_id:
            client.fabric_id = event.fabric_id
            changed = True
        return client, changed

    async def _resolve_client_name(
        self,
        session,  # noqa: ANN001
        *,
        client_id: str,
        existing_name: str | None,
    ) -> str:
        desired_state = await session.get(ConfigDesiredStateRecord, client_id)
        if desired_state is not None:
            display_name = desired_state.tenant_metadata.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                return display_name.strip()
        if existing_name is not None and existing_name.strip():
            return existing_name
        return f"Client {client_id[:8]}"

    async def _apply_asset_projection(
        self,
        session,  # noqa: ANN001
        event: PortfolioEvent,
    ) -> tuple[PortfolioAsset | None, bool]:
        if event.event_type == "asset.upserted":
            asset_ref = AssetRef.model_validate(event.payload["asset"])
            return await self._upsert_asset(
                session,
                asset_ref=asset_ref,
                observed_health=None,
                observed_at=None,
                last_alert_at=None,
                updated_at=event.occurred_at,
            )

        if event.event_type in {"network.alert.raised", "host.alert.raised"}:
            alert = AlertRaised.model_validate(event.payload["alert"])
            return await self._upsert_asset(
                session,
                asset_ref=alert.asset,
                observed_health=None,
                observed_at=None,
                last_alert_at=alert.occurred_at,
                updated_at=alert.occurred_at,
            )

        if event.event_type in {
            "asset.health.changed",
            "network.snapshot.recorded",
            "host.snapshot.recorded",
        }:
            snapshot = HealthSnapshot.model_validate(event.payload["snapshot"])
            return await self._upsert_asset(
                session,
                asset_ref=snapshot.asset,
                observed_health=snapshot.health_state,
                observed_at=snapshot.collected_at,
                last_alert_at=None,
                updated_at=snapshot.collected_at,
            )

        raise ValueError(f"unsupported portfolio event_type: {event.event_type}")

    async def _upsert_asset(
        self,
        session,  # noqa: ANN001
        *,
        asset_ref: AssetRef,
        observed_health: HealthState | None,
        observed_at,
        last_alert_at,
        updated_at,
    ) -> tuple[PortfolioAsset, bool]:
        asset = await session.get(PortfolioAsset, asset_ref.asset_id)
        previous_state = (
            None
            if asset is None
            else (
                asset.client_id,
                asset.fabric_id,
                asset.asset_kind,
                asset.vendor,
                asset.model,
                asset.hostname,
                asset.mgmt_address,
                asset.site,
                tuple(asset.tags),
                asset.health_state,
                asset.health_observed_at,
                asset.last_alert_at,
                asset.updated_at,
            )
        )
        if asset is None:
            asset = PortfolioAsset(
                asset_id=asset_ref.asset_id,
                client_id=asset_ref.client_id,
                fabric_id=asset_ref.fabric_id,
                asset_kind=asset_ref.asset_kind.value,
                vendor=asset_ref.vendor,
                model=asset_ref.model,
                hostname=asset_ref.hostname,
                mgmt_address=asset_ref.mgmt_address,
                site=asset_ref.site,
                tags=list(asset_ref.tags),
                health_state=HealthState.UNKNOWN.value,
                health_observed_at=None,
                last_alert_at=None,
                updated_at=updated_at,
            )
            session.add(asset)
        else:
            asset.client_id = asset_ref.client_id
            asset.fabric_id = asset_ref.fabric_id
            asset.asset_kind = asset_ref.asset_kind.value
            asset.vendor = asset_ref.vendor
            asset.model = asset_ref.model
            asset.hostname = asset_ref.hostname
            asset.mgmt_address = asset_ref.mgmt_address
            asset.site = asset_ref.site
            asset.tags = list(asset_ref.tags)
            asset.updated_at = max(asset.updated_at, updated_at)

        if (
            observed_health is not None
            and observed_at is not None
            and (
                asset.health_observed_at is None
                or observed_at >= asset.health_observed_at
            )
        ):
            asset.health_state = observed_health.value
            asset.health_observed_at = observed_at
            asset.updated_at = max(asset.updated_at, observed_at)

        if last_alert_at is not None and (
            asset.last_alert_at is None or last_alert_at >= asset.last_alert_at
        ):
            asset.last_alert_at = last_alert_at
            asset.updated_at = max(asset.updated_at, last_alert_at)

        changed = previous_state != (
            asset.client_id,
            asset.fabric_id,
            asset.asset_kind,
            asset.vendor,
            asset.model,
            asset.hostname,
            asset.mgmt_address,
            asset.site,
            tuple(asset.tags),
            asset.health_state,
            asset.health_observed_at,
            asset.last_alert_at,
            asset.updated_at,
        )
        return asset, changed

    async def _count_critical_assets(self, session, client_id: str) -> int:  # noqa: ANN001
        count = await session.scalar(
            select(func.count())
            .select_from(PortfolioAsset)
            .where(
                PortfolioAsset.client_id == client_id,
                PortfolioAsset.health_state == HealthState.CRITICAL.value,
            )
        )
        return int(count or 0)

    def _build_stream_events(
        self,
        *,
        client: PortfolioClient | None,
        asset: PortfolioAsset | None,
        activity: PortfolioActivityEvent,
        occurred_at: datetime,
    ) -> list[OpsStreamEvent]:
        stream_events = [
            self._stream_service.build_event(
                event_name="activity.appended",
                client_id=activity.client_id,
                incident_id=activity.incident_id,
                chat_session_id=None,
                subject_type="portfolio_activity",
                subject_id=str(activity.activity_id),
                payload=serialize_activity(activity),
                occurred_at=activity.occurred_at,
            ),
        ]
        if client is not None:
            stream_events.insert(
                0,
                self._stream_service.build_event(
                    event_name="client.updated",
                    client_id=client.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="portfolio_client",
                    subject_id=client.client_id,
                    payload=serialize_client_summary(client),
                    occurred_at=occurred_at,
                ),
            )
        if asset is not None:
            stream_events.append(
                self._stream_service.build_event(
                    event_name="asset.updated",
                    client_id=asset.client_id,
                    incident_id=None,
                    chat_session_id=None,
                    subject_type="portfolio_asset",
                    subject_id=asset.asset_id,
                    payload=serialize_asset(asset),
                    occurred_at=occurred_at,
                )
            )
        return stream_events


__all__ = ["PortfolioIngestService", "ProjectionWriteResult"]
