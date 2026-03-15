"""Durable alerting store adapters for the in-process ops app."""

from __future__ import annotations

from sqlalchemy import select

from mas_msp_contracts import Severity
from mas_msp_core import (
    AlertConditionPhase,
    AlertConditionRecord,
    AppliedAlertConfiguration,
)

from mas_ops_api.db.models import (
    AlertConditionStateRecord,
    AppliedAlertPolicyRecord,
)
from mas_ops_api.db.session import Database


class OpsPlaneAppliedAlertPolicyStore:
    """Persist applied alert policy for restart-safe local runtime behavior."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def get_applied_policy(
        self,
        client_id: str,
    ) -> AppliedAlertConfiguration | None:
        async with self._database.session_factory() as session:
            row = await session.get(AppliedAlertPolicyRecord, client_id)
            if row is None:
                return None
            return AppliedAlertConfiguration.model_validate(row.configuration)

    async def apply_configuration(
        self,
        configuration: AppliedAlertConfiguration,
    ) -> None:
        async with self._database.session_factory() as session:
            row = await session.get(AppliedAlertPolicyRecord, configuration.client_id)
            if row is None:
                row = AppliedAlertPolicyRecord(client_id=configuration.client_id)
                session.add(row)
            row.configuration = configuration.model_dump(mode="json")
            await session.commit()


class OpsPlaneAlertConditionStore:
    """Persist alert condition state for restart-safe local runtime behavior."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def get_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> AlertConditionRecord | None:
        async with self._database.session_factory() as session:
            row = await session.get(
                AlertConditionStateRecord,
                (client_id, asset_id, condition_key),
            )
            if row is None:
                return None
            return _condition_record(row)

    async def put_condition(self, record: AlertConditionRecord) -> None:
        async with self._database.session_factory() as session:
            row = await session.get(
                AlertConditionStateRecord,
                (record.client_id, record.asset_id, record.condition_key),
            )
            if row is None:
                row = AlertConditionStateRecord(
                    client_id=record.client_id,
                    asset_id=record.asset_id,
                    condition_key=record.condition_key,
                    correlation_key=record.correlation_key,
                    category=record.category,
                    title=record.title,
                    severity=record.severity.value,
                    source_kind=record.source_kind,
                    normalized_facts=dict(record.normalized_facts),
                    phase=record.phase.value,
                    open_observation_count=record.open_observation_count,
                    close_observation_count=record.close_observation_count,
                    open_after_polls=record.open_after_polls,
                    close_after_polls=record.close_after_polls,
                    cooldown_seconds=record.cooldown_seconds,
                    opened_at=record.opened_at,
                    resolved_at=record.resolved_at,
                    last_observed_at=record.last_observed_at,
                    last_surfaced_at=record.last_surfaced_at,
                )
                session.add(row)
            else:
                row.correlation_key = record.correlation_key
                row.category = record.category
                row.title = record.title
                row.severity = record.severity.value
                row.source_kind = record.source_kind
                row.normalized_facts = dict(record.normalized_facts)
                row.phase = record.phase.value
                row.open_observation_count = record.open_observation_count
                row.close_observation_count = record.close_observation_count
                row.open_after_polls = record.open_after_polls
                row.close_after_polls = record.close_after_polls
                row.cooldown_seconds = record.cooldown_seconds
                row.opened_at = record.opened_at
                row.resolved_at = record.resolved_at
                row.last_observed_at = record.last_observed_at
                row.last_surfaced_at = record.last_surfaced_at
            await session.commit()

    async def delete_condition(
        self,
        *,
        client_id: str,
        asset_id: str,
        condition_key: str,
    ) -> None:
        async with self._database.session_factory() as session:
            row = await session.get(
                AlertConditionStateRecord,
                (client_id, asset_id, condition_key),
            )
            if row is None:
                return
            await session.delete(row)
            await session.commit()

    async def list_conditions_for_asset(
        self,
        *,
        client_id: str,
        asset_id: str,
    ) -> list[AlertConditionRecord]:
        async with self._database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(AlertConditionStateRecord)
                    .where(
                        AlertConditionStateRecord.client_id == client_id,
                        AlertConditionStateRecord.asset_id == asset_id,
                    )
                    .order_by(
                        AlertConditionStateRecord.condition_key.asc(),
                        AlertConditionStateRecord.last_observed_at.asc(),
                    )
                )
            ).all()
            return [_condition_record(row) for row in rows]


def _condition_record(row: AlertConditionStateRecord) -> AlertConditionRecord:
    return AlertConditionRecord(
        client_id=row.client_id,
        asset_id=row.asset_id,
        condition_key=row.condition_key,
        correlation_key=row.correlation_key,
        category=row.category,
        title=row.title,
        severity=Severity(row.severity),
        source_kind=row.source_kind,
        normalized_facts=dict(row.normalized_facts),
        phase=AlertConditionPhase(row.phase),
        open_observation_count=row.open_observation_count,
        close_observation_count=row.close_observation_count,
        open_after_polls=row.open_after_polls,
        close_after_polls=row.close_after_polls,
        cooldown_seconds=row.cooldown_seconds,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        last_observed_at=row.last_observed_at,
        last_surfaced_at=row.last_surfaced_at,
    )
__all__ = [
    "OpsPlaneAlertConditionStore",
    "OpsPlaneAppliedAlertPolicyStore",
]
