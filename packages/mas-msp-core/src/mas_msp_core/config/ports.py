"""Protocol boundaries for desired-state validation and apply workflows."""

from __future__ import annotations

from typing import Protocol

from mas_msp_contracts import (
    ConfigApplyRequested,
    ConfigApplyResult,
    ConfigDesiredState,
)

from .models import ConfigApplyRunRecord


class DesiredStateStore(Protocol):
    """Load desired-state documents for deterministic validation and apply."""

    async def get_desired_state(self, client_id: str) -> ConfigDesiredState | None: ...


class ConfigRunStore(Protocol):
    """Persist validation results, apply runs, and ordered apply progress."""

    async def get_apply_run(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord | None: ...

    async def attach_approval(
        self,
        config_apply_run_id: str,
        *,
        approval_id: str,
    ) -> ConfigApplyRunRecord: ...

    async def start_validation(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord: ...

    async def start_apply(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRunRecord: ...

    async def complete_apply(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyResult: ...

    async def fail_apply(
        self,
        config_apply_run_id: str,
        *,
        error_summary: str,
    ) -> ConfigApplyResult: ...

    async def cancel_apply(
        self,
        config_apply_run_id: str,
        *,
        reason: str,
    ) -> ConfigApplyResult: ...

    async def record_apply_step(
        self,
        config_apply_run_id: str,
        *,
        step_name: str,
        outcome: str,
        details: dict[str, object],
    ) -> None: ...

    async def record_validation_result(
        self,
        result,
    ) -> None: ...  # noqa: ANN401

    async def get_apply_request(
        self,
        config_apply_run_id: str,
    ) -> ConfigApplyRequested | None: ...


__all__ = ["ConfigRunStore", "DesiredStateStore"]
