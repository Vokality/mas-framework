"""Serializers for projection-backed stream payloads."""

from __future__ import annotations

from typing import Any

from mas_ops_api.db.models import (
    IncidentEvidenceBundleRecord,
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
    PortfolioIncident,
)


def serialize_client_summary(client: PortfolioClient) -> dict[str, object]:
    """Return the client summary payload emitted on client stream updates."""

    return {
        "client_id": client.client_id,
        "fabric_id": client.fabric_id,
        "name": client.name,
        "open_alert_count": client.open_alert_count,
        "critical_asset_count": client.critical_asset_count,
        "updated_at": _serialize_datetime(client.updated_at),
    }


def serialize_asset(asset: PortfolioAsset) -> dict[str, object]:
    """Return the full asset payload emitted on asset stream updates."""

    return {
        "asset_id": asset.asset_id,
        "client_id": asset.client_id,
        "fabric_id": asset.fabric_id,
        "asset_kind": asset.asset_kind,
        "vendor": asset.vendor,
        "model": asset.model,
        "hostname": asset.hostname,
        "mgmt_address": asset.mgmt_address,
        "site": asset.site,
        "tags": list(asset.tags),
        "health_state": asset.health_state,
        "health_observed_at": _serialize_optional_datetime(asset.health_observed_at),
        "last_alert_at": _serialize_optional_datetime(asset.last_alert_at),
        "updated_at": _serialize_datetime(asset.updated_at),
    }


def serialize_activity(activity: PortfolioActivityEvent) -> dict[str, Any]:
    """Return the full activity payload emitted on activity stream updates."""

    return {
        "activity_id": activity.activity_id,
        "source_event_id": activity.source_event_id,
        "client_id": activity.client_id,
        "fabric_id": activity.fabric_id,
        "incident_id": activity.incident_id,
        "asset_id": activity.asset_id,
        "event_type": activity.event_type,
        "subject_type": activity.subject_type,
        "subject_id": activity.subject_id,
        "payload": activity.payload,
        "occurred_at": _serialize_datetime(activity.occurred_at),
    }


def serialize_incident(incident: PortfolioIncident) -> dict[str, object]:
    """Return the incident payload emitted on incident stream updates."""

    return {
        "incident_id": incident.incident_id,
        "client_id": incident.client_id,
        "fabric_id": incident.fabric_id,
        "state": incident.state,
        "severity": incident.severity,
        "summary": incident.summary,
        "recommended_actions": list(incident.recommended_actions),
        "opened_at": _serialize_datetime(incident.opened_at),
        "updated_at": _serialize_datetime(incident.updated_at),
    }


def serialize_evidence_bundle(
    evidence: IncidentEvidenceBundleRecord,
) -> dict[str, object]:
    """Return a serialized evidence bundle payload."""

    return {
        "evidence_bundle_id": evidence.evidence_bundle_id,
        "incident_id": evidence.incident_id,
        "asset_id": evidence.asset_id,
        "client_id": evidence.client_id,
        "fabric_id": evidence.fabric_id,
        "collected_at": _serialize_datetime(evidence.collected_at),
        "summary": evidence.summary,
        "items": list(evidence.items),
    }


def _serialize_datetime(value) -> str:  # noqa: ANN001
    return value.isoformat().replace("+00:00", "Z")


def _serialize_optional_datetime(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    return _serialize_datetime(value)


__all__ = [
    "serialize_evidence_bundle",
    "serialize_activity",
    "serialize_asset",
    "serialize_client_summary",
    "serialize_incident",
]
