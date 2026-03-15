"""Serializers for projection-backed stream payloads."""

from __future__ import annotations

from typing import Any

from mas_ops_api.db.models import (
    PortfolioActivityEvent,
    PortfolioAsset,
    PortfolioClient,
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


def _serialize_datetime(value) -> str:  # noqa: ANN001
    return value.isoformat().replace("+00:00", "Z")


def _serialize_optional_datetime(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    return _serialize_datetime(value)


__all__ = [
    "serialize_activity",
    "serialize_asset",
    "serialize_client_summary",
]
