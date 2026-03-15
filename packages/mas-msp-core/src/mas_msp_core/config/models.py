"""Config run models used by the phase-4 deployer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mas_msp_contracts import ConfigApplyState


@dataclass(frozen=True, slots=True)
class ConfigApplyRunRecord:
    """Persisted apply-run state exposed to the config deployer."""

    config_apply_run_id: str
    client_id: str
    desired_state_version: int
    status: ConfigApplyState
    requested_by_user_id: str
    requested_at: datetime
    approval_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_summary: str | None = None


__all__ = ["ConfigApplyRunRecord"]
