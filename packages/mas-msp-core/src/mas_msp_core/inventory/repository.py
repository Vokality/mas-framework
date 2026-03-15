"""Deterministic asset binding for network visibility events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from mas_msp_contracts import AlertRaised, AssetRef, HealthSnapshot, HealthState

from mas_msp_core.messages import PortfolioPublish


@dataclass(slots=True)
class InventoryAssetRecord:
    """Resolved inventory record and its latest known health summary."""

    asset: AssetRef
    health_state: HealthState = HealthState.UNKNOWN
    health_collected_at: datetime | None = None


class InventoryRepository:
    """Own asset binding precedence and health-state tracking."""

    def __init__(self) -> None:
        self._assets: dict[str, InventoryAssetRecord] = {}
        self._address_index: dict[tuple[str, str], str] = {}
        self._serial_index: dict[tuple[str, str], str] = {}
        self._hostname_index: dict[tuple[str, str], str] = {}

    def process_alert(self, alert: AlertRaised) -> PortfolioPublish:
        """Resolve an alert to an authoritative asset record."""

        resolved_asset, asset_upserted = self._resolve_asset(
            alert.asset,
            self._extract_serial_from_alert(alert),
        )
        resolved_alert = alert.model_copy(update={"asset": resolved_asset})
        return PortfolioPublish(
            asset=resolved_asset,
            asset_upserted=asset_upserted,
            health_changed=False,
            source=resolved_alert,
        )

    def process_snapshot(self, snapshot: HealthSnapshot) -> PortfolioPublish:
        """Resolve a snapshot and update the latest health summary."""

        resolved_asset, asset_upserted = self._resolve_asset(
            snapshot.asset,
            self._extract_serial_from_snapshot(snapshot),
        )
        record = self._assets[resolved_asset.asset_id]
        health_changed = False
        if (
            record.health_collected_at is None
            or snapshot.collected_at > record.health_collected_at
        ):
            health_changed = record.health_state is not snapshot.health_state
            record.health_state = snapshot.health_state
            record.health_collected_at = snapshot.collected_at
        resolved_snapshot = snapshot.model_copy(update={"asset": resolved_asset})
        return PortfolioPublish(
            asset=resolved_asset,
            asset_upserted=asset_upserted,
            health_changed=health_changed,
            source=resolved_snapshot,
        )

    def get_asset(self, asset_id: str) -> InventoryAssetRecord | None:
        """Return one resolved inventory record."""

        return self._assets.get(asset_id)

    def list_assets_for_client(self, client_id: str) -> list[InventoryAssetRecord]:
        """Return resolved assets for one client in stable order."""

        records = [
            record
            for record in self._assets.values()
            if record.asset.client_id == client_id
        ]
        return sorted(
            records,
            key=lambda record: (
                record.asset.hostname or "",
                record.asset.mgmt_address or "",
                record.asset.asset_id,
            ),
        )

    def _resolve_asset(
        self,
        candidate: AssetRef,
        serial: str | None,
    ) -> tuple[AssetRef, bool]:
        matched_asset_id = self._find_existing_asset_id(candidate, serial)
        if matched_asset_id is None:
            resolved_asset = candidate.model_copy(update={"asset_id": str(uuid4())})
            record = InventoryAssetRecord(asset=resolved_asset)
            self._assets[resolved_asset.asset_id] = record
            self._update_indexes(record.asset, serial)
            return resolved_asset, True

        record = self._assets[matched_asset_id]
        merged_asset = self._merge_asset(record.asset, candidate)
        asset_upserted = merged_asset != record.asset
        record.asset = merged_asset
        self._update_indexes(record.asset, serial)
        return record.asset, asset_upserted

    def _find_existing_asset_id(
        self,
        candidate: AssetRef,
        serial: str | None,
    ) -> str | None:
        client_id = candidate.client_id
        if candidate.mgmt_address is not None:
            key = (client_id, self._normalize_key(candidate.mgmt_address))
            if key in self._address_index:
                return self._address_index[key]
        if serial is not None:
            key = (client_id, self._normalize_key(serial))
            if key in self._serial_index:
                return self._serial_index[key]
        if candidate.hostname is not None:
            key = (client_id, self._normalize_key(candidate.hostname))
            if key in self._hostname_index:
                return self._hostname_index[key]
        return None

    def _merge_asset(self, existing: AssetRef, candidate: AssetRef) -> AssetRef:
        merged_tags = list(dict.fromkeys([*existing.tags, *candidate.tags]))
        return existing.model_copy(
            update={
                "asset_kind": candidate.asset_kind,
                "vendor": candidate.vendor or existing.vendor,
                "model": candidate.model or existing.model,
                "hostname": candidate.hostname or existing.hostname,
                "mgmt_address": candidate.mgmt_address or existing.mgmt_address,
                "site": candidate.site or existing.site,
                "tags": merged_tags,
            }
        )

    def _update_indexes(self, asset: AssetRef, serial: str | None) -> None:
        client_id = asset.client_id
        if asset.mgmt_address is not None:
            self._address_index[
                (client_id, self._normalize_key(asset.mgmt_address))
            ] = asset.asset_id
        if serial is not None:
            self._serial_index[(client_id, self._normalize_key(serial))] = (
                asset.asset_id
            )
        if asset.hostname is not None:
            self._hostname_index[(client_id, self._normalize_key(asset.hostname))] = (
                asset.asset_id
            )

    @staticmethod
    def _normalize_key(value: str) -> str:
        return value.strip().lower()

    @staticmethod
    def _extract_serial_from_alert(alert: AlertRaised) -> str | None:
        serial = alert.normalized_facts.get("serial")
        return serial if isinstance(serial, str) and serial.strip() else None

    @staticmethod
    def _extract_serial_from_snapshot(snapshot: HealthSnapshot) -> str | None:
        serial = snapshot.metrics.get("serial")
        return serial if isinstance(serial, str) and serial.strip() else None


__all__ = ["InventoryAssetRecord", "InventoryRepository"]
