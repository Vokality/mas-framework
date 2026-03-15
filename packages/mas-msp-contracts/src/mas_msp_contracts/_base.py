"""Shared Pydantic base classes for MSP contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Strict base model for shared MSP contracts."""

    model_config = ConfigDict(extra="forbid")
