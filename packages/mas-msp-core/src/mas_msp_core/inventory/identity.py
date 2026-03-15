"""Inventory-owned stable asset identity helpers."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from mas_msp_contracts import AssetRef


def build_inventory_asset_id(
    candidate: AssetRef,
    *,
    serial: str | None,
) -> str:
    """Return a stable inventory-owned asset id when identity facts are available."""

    identity = _identity_key(candidate, serial=serial)
    if identity is None:
        return candidate.asset_id
    digest = sha256(
        (
            "inventory-asset:"
            f"{candidate.asset_kind.value}:"
            f"{candidate.client_id}:"
            f"{candidate.fabric_id}:"
            f"{identity[0]}:"
            f"{identity[1]}"
        ).encode("utf-8")
    )
    return str(UUID(bytes=digest.digest()[:16], version=4))


def _identity_key(
    candidate: AssetRef,
    *,
    serial: str | None,
) -> tuple[str, str] | None:
    if candidate.mgmt_address is not None:
        normalized = _normalize_key(candidate.mgmt_address)
        if normalized:
            return "mgmt_address", normalized
    if serial is not None:
        normalized = _normalize_key(serial)
        if normalized:
            return "serial", normalized
    if candidate.hostname is not None:
        normalized = _normalize_key(candidate.hostname)
        if normalized:
            return "hostname", normalized
    return None


def _normalize_key(value: str) -> str:
    return value.strip().lower()


__all__ = ["build_inventory_asset_id"]
