"""Stores used by the core alerting workflow."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from mas_msp_contracts import JSONObject, Severity

from .models import AppliedAlertConfiguration


class AlertConditionPhase(StrEnum):
    """Lifecycle phase for one tracked alert condition."""

    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class AlertConditionRecord:
    """Tracked state for one alert condition tied to one resolved asset."""

    client_id: str
    asset_id: str
    condition_key: str
    correlation_key: str
    category: str
    title: str
    severity: Severity
    source_kind: str
    normalized_facts: JSONObject
    phase: AlertConditionPhase
    open_observation_count: int
    close_observation_count: int
    open_after_polls: int
    close_after_polls: int
    cooldown_seconds: int
    opened_at: datetime | None
    resolved_at: datetime | None
    last_observed_at: datetime
    last_surfaced_at: datetime | None = None


class AppliedAlertPolicyStore(Protocol):
    """Load or materialize applied alert policy inside one fabric."""

    async def get_applied_policy(
        self,
        client_id: str,
    ) -> AppliedAlertConfiguration | None: ...

    async def apply_configuration(
        self,
        configuration: AppliedAlertConfiguration,
    ) -> None: ...


class AlertConditionStore(Protocol):
    """Persist tracked alert condition state across alerting evaluations."""

    async def get_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> AlertConditionRecord | None: ...

    async def put_condition(self, record: AlertConditionRecord) -> None: ...

    async def delete_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> None: ...

    async def list_conditions_for_asset(
        self,
        *,
        client_id: str,
        asset_id: str,
    ) -> list[AlertConditionRecord]: ...


@dataclass(slots=True)
class InMemoryAppliedAlertPolicyStore:
    """Local in-process applied alert policy store for tests and dogfood."""

    _configurations: dict[str, AppliedAlertConfiguration] = field(default_factory=dict)

    async def get_applied_policy(
        self,
        client_id: str,
    ) -> AppliedAlertConfiguration | None:
        return self._configurations.get(client_id)

    async def apply_configuration(
        self,
        configuration: AppliedAlertConfiguration,
    ) -> None:
        self._configurations[configuration.client_id] = configuration


@dataclass(slots=True)
class InMemoryAlertConditionStore:
    """Local in-process condition store that survives agent replacement."""

    _conditions: dict[tuple[str, str, str], AlertConditionRecord] = field(
        default_factory=dict
    )

    async def get_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> AlertConditionRecord | None:
        return self._conditions.get((client_id, asset_id, condition_key))

    async def put_condition(self, record: AlertConditionRecord) -> None:
        self._conditions[(record.client_id, record.asset_id, record.condition_key)] = (
            record
        )

    async def delete_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> None:
        self._conditions.pop((client_id, asset_id, condition_key), None)

    async def list_conditions_for_asset(
        self,
        *,
        client_id: str,
        asset_id: str,
    ) -> list[AlertConditionRecord]:
        records: Iterable[AlertConditionRecord] = self._conditions.values()
        return sorted(
            (
                record
                for record in records
                if record.client_id == client_id and record.asset_id == asset_id
            ),
            key=lambda record: (record.condition_key, record.last_observed_at),
        )


__all__ = [
    "AlertConditionPhase",
    "AlertConditionRecord",
    "AlertConditionStore",
    "AppliedAlertPolicyStore",
    "InMemoryAlertConditionStore",
    "InMemoryAppliedAlertPolicyStore",
]
