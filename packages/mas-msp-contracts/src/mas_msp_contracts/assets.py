"""Asset and credential contracts."""

from __future__ import annotations

from pydantic import Field

from ._base import ContractModel
from .enums import AssetKind
from .types import AssetId, ClientId, FabricId, JSONObject, NonEmptyStr


class AssetRef(ContractModel):
    """Stable reference to a managed asset."""

    asset_id: AssetId
    client_id: ClientId
    fabric_id: FabricId
    asset_kind: AssetKind
    vendor: NonEmptyStr | None = None
    model: NonEmptyStr | None = None
    hostname: NonEmptyStr | None = None
    mgmt_address: NonEmptyStr | None = None
    site: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)


class CredentialRef(ContractModel):
    """Reference to credential material stored outside MSP contracts."""

    credential_ref: NonEmptyStr
    provider_kind: NonEmptyStr
    scope: JSONObject = Field(default_factory=dict)
    purpose: NonEmptyStr
    secret_path: NonEmptyStr


__all__ = ["AssetRef", "CredentialRef"]
