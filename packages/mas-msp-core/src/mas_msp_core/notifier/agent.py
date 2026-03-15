"""Notifier transport agent for operator-visible incident persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from mas_msp_contracts import AlertRaised, EvidenceBundle, IncidentRecord, IncidentState
from mas_msp_contracts import JSONObject, Severity

from mas_msp_core.incidents import IncidentContextReader, NotifierTransport
from .delivery import NotificationService


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
        notification_service: NotificationService | None = None,
        incident_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._incident_context_reader = incident_context_reader
        self._transport = transport
        self._notification_service = notification_service
        self._incident_id_factory = incident_id_factory or (lambda: str(uuid4()))

    async def open_or_update_incident_from_alert(
        self,
        alert: AlertRaised,
        *,
        correlation_key: str | None = None,
    ) -> IncidentRecord:
        """Ensure one active incident exists for the alert and affected asset."""

        resolved_correlation_key = correlation_key or _correlation_key_from_alert(alert)
        existing = await self._incident_context_reader.find_active_incident(
            client_id=alert.client_id,
            correlation_key=resolved_correlation_key,
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
        severity = (
            alert.severity
            if existing is None
            else _max_severity(existing.severity, alert.severity)
        )
        state = IncidentState.OPEN if existing is None else existing.state
        source = "visibility" if existing is None else "visibility_update"
        incident = await self._transport.record_visibility_incident(
            incident_id=incident_id,
            client_id=alert.client_id,
            fabric_id=alert.fabric_id,
            correlation_key=resolved_correlation_key,
            summary=alert.title,
            severity=severity,
            state=state,
            asset_ids=asset_ids,
            asset_refs=[alert.asset],
            occurred_at=alert.occurred_at,
            source=source,
            source_event_id=f"incident-alert:{alert.alert_id}",
        )
        if self._notification_service is not None:
            event_kind = _notification_event_kind(existing, candidate_severity=alert.severity)
            if event_kind is not None:
                await self._record_notification_attempts(
                    alert=alert,
                    incident=incident,
                    event_kind=event_kind,
                )
        return incident

    async def resolve_incident_for_condition(
        self,
        *,
        alert: AlertRaised,
        correlation_key: str,
        resolved_at: datetime,
    ) -> IncidentRecord | None:
        """Resolve the active incident bound to one alert condition, if any."""

        existing = await self._incident_context_reader.find_active_incident(
            client_id=alert.client_id,
            correlation_key=correlation_key,
            asset_id=alert.asset.asset_id,
        )
        if existing is None:
            return None
        incident = await self._transport.persist_summary(
            incident_id=existing.incident_id,
            summary=existing.summary,
            severity=existing.severity,
            state=IncidentState.RESOLVED,
            recommended_actions=None,
            asset_ids=list(existing.asset_ids),
        )
        await self.append_activity(
            incident_id=incident.incident_id,
            event_type="incident.resolved",
            payload={
                "correlation_key": correlation_key,
                "resolved_at": resolved_at.isoformat().replace("+00:00", "Z"),
                "summary": alert.title,
            },
            asset_id=alert.asset.asset_id,
            occurred_at=resolved_at,
        )
        if self._notification_service is not None:
            await self._record_notification_attempts(
                alert=alert,
                incident=incident,
                event_kind="resolved",
            )
        return incident

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

    async def _record_notification_attempts(
        self,
        *,
        alert: AlertRaised,
        incident: IncidentRecord,
        event_kind: str,
    ) -> None:
        if self._notification_service is None:
            return
        try:
            attempts = await self._notification_service.deliver_alert(
                alert=alert,
                incident=incident,
                event_kind=event_kind,
            )
        except Exception as exc:
            await self.append_activity(
                incident_id=incident.incident_id,
                event_type="notification.failed",
                payload={
                    "event_kind": event_kind,
                    "route_kind": "internal",
                    "target": "notification-service",
                    "outcome": "failed",
                    "detail": str(exc),
                },
                asset_id=alert.asset.asset_id,
                occurred_at=alert.occurred_at,
            )
            return
        for attempt in attempts:
            await self.append_activity(
                incident_id=incident.incident_id,
                event_type=(
                    "notification.delivered"
                    if attempt.outcome == "succeeded"
                    else "notification.failed"
                ),
                payload={
                    "event_kind": event_kind,
                    "route_kind": attempt.route_kind,
                    "target": attempt.target,
                    "outcome": attempt.outcome,
                    "detail": attempt.detail,
                },
                asset_id=alert.asset.asset_id,
                occurred_at=alert.occurred_at,
            )


def _max_severity(current: Severity, candidate: Severity) -> Severity:
    if SEVERITY_ORDER[candidate.value] > SEVERITY_ORDER[current.value]:
        return candidate
    return current


def _merge_asset_ids(existing_asset_ids: list[str], asset_id: str) -> list[str]:
    if asset_id in existing_asset_ids:
        return list(existing_asset_ids)
    return [*existing_asset_ids, asset_id]


def _correlation_key_from_alert(alert: AlertRaised) -> str | None:
    correlation_key = alert.normalized_facts.get("correlation_key")
    return correlation_key if isinstance(correlation_key, str) and correlation_key else None


def _notification_event_kind(
    existing: IncidentRecord | None,
    *,
    candidate_severity: Severity,
) -> str | None:
    if existing is None:
        return "opened"
    if _max_severity(existing.severity, candidate_severity) is candidate_severity and (
        candidate_severity is not existing.severity
    ):
        return "escalated"
    return None


__all__ = ["NotifierTransportAgent"]
