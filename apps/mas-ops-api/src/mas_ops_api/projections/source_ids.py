"""Stable projection source identifiers for portfolio event idempotency."""

from __future__ import annotations

from hashlib import sha256

from mas_msp_contracts import AlertRaised, HealthSnapshot, PortfolioEvent


def build_projection_source_id(event: PortfolioEvent) -> str:
    """Return a stable idempotency key for one logical portfolio event."""

    source_type, source_id = _resolve_source_reference(event)
    stable_reference = (
        f"event_type={event.event_type}|source_type={source_type}|source_id={source_id}"
    )
    return sha256(stable_reference.encode("utf-8")).hexdigest()


def _resolve_source_reference(event: PortfolioEvent) -> tuple[str, str]:
    if event.event_type == "network.alert.raised":
        alert = AlertRaised.model_validate(event.payload["alert"])
        return "alert", alert.alert_id
    if event.event_type in {"asset.health.changed", "network.snapshot.recorded"}:
        snapshot = HealthSnapshot.model_validate(event.payload["snapshot"])
        return "snapshot", snapshot.snapshot_id
    if event.event_type == "asset.upserted":
        source_reference = event.payload.get("source_reference")
        if isinstance(source_reference, dict):
            source_type = source_reference.get("source_type")
            source_id = source_reference.get("source_id")
            if isinstance(source_type, str) and isinstance(source_id, str):
                return source_type, source_id
    return "portfolio_event", event.event_id


__all__ = ["build_projection_source_id"]
