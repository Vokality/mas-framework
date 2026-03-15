"""Phase 2 visibility messages exchanged between deterministic MSP agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mas_msp_contracts import AlertRaised, AssetRef, HealthSnapshot


class PortfolioPublish(BaseModel):
    """Resolved visibility payload emitted from inventory to the ops bridge."""

    asset: AssetRef
    asset_upserted: bool = False
    health_changed: bool = False
    source: AlertRaised | HealthSnapshot

    model_config = ConfigDict(extra="forbid")


__all__ = ["PortfolioPublish"]
