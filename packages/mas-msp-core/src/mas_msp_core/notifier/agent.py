"""Notifier transport agent for operator-visible incident persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from mas_msp_contracts import AlertRaised, EvidenceBundle, IncidentRecord, IncidentState
from mas_msp_contracts import JSONObject, Severity

from mas_msp_core.incidents import IncidentContextReader, NotifierTransport


SEVERITY_ORDER = {
    Severity.INFO.value: 0,
    Severity.WARNING.value: 1,
    Severity.MINOR.value: 2,
    Severity.MAJOR.value: 3,
    Severity.CRITICAL.value: 4,
}


class NotifierTransportAgent:
    """Persist incidents, evidence, and activity into operator-visible stores."""

    def __init__(
        self,
        *,
        incident_context_reader: IncidentContextReader,
        transport: NotifierTransport,
        incident_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._transport = transport
        self._incident_id_factory = incident_id_factory or (lambda: str(uuid4()))

    async def open_or_update_incident_from_alert(
        self,
        alert: AlertRaised,
    ) -> IncidentRecord:
        """Ensure one active incident exists for the alert and affected asset."""

        existing = await self._incident_context_reader.find_active_incident_for_asset(
            client_id=alert.client_id,
            asset_id=alert.asset.asset_id,
        )
        incident_id = (
            self._incident_id_factory() if existing is None else existing.incident_id
        )
        asset_ids = (
            [alert.asset.asset_id]
            if existing is None
            else _merge_asset_ids(existing.asset_ids, alert.asset.asset_id)
        )
        severity = alert.severity if existing is None else _max_severity(
            existing.severity, alert.severity
        )
        state = IncidentState.OPEN if existing is None else existing.state
        source = "visibility" if existing is None else "visibility_update"
        return await self._transport.record_visibility_incident(
            incident_id=incident_id,
            client_id=alert.client_id,
            fabric_id=alert.fabric_id,
            summary=alert.title,
            severity=severity,
            state=state,
            asset_ids=asset_ids,
            occurred_at=alert.occurred_at,
            source=source,
            source_event_id=f"incident-alert:{alert.alert_id}",
        )

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
        """Persist the latest incident summary and recommendations."""

        return await self._transport.persist_summary(
            incident_id=incident_id,
            summary=summary,
            severity=severity,
            state=state,
            recommended_actions=recommended_actions,
            asset_ids=asset_ids,
        )

    async def append_activity(
        self,
        *,
        incident_id: str,
        event_type: str,
        payload: JSONObject,
        asset_id: str | None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Append one operator-visible incident activity entry."""

        await self._transport.append_activity(
            incident_id=incident_id,
            event_type=event_type,
            payload=payload,
            asset_id=asset_id,
            occurred_at=occurred_at,
        )

    async def persist_evidence_bundle(
        self,
        *,
        bundle: EvidenceBundle,
        client_id: str,
        fabric_id: str,
    ) -> None:
        """Persist one evidence bundle for later operator inspection."""

        await self._transport.persist_evidence_bundle(
            bundle=bundle,
            client_id=client_id,
            fabric_id=fabric_id,
        )


def _max_severity(current: Severity, candidate: Severity) -> Severity:
    if SEVERITY_ORDER[candidate.value] > SEVERITY_ORDER[current.value]:
        return candidate
    return current


def _merge_asset_ids(existing_asset_ids: list[str], asset_id: str) -> list[str]:
    if asset_id in existing_asset_ids:
        return list(existing_asset_ids)
    return [*existing_asset_ids, asset_id]


__all__ = ["NotifierTransportAgent"]
