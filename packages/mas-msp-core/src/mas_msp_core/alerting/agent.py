"""Alert evaluation and dedupe coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from mas_msp_contracts import AlertRaised, HealthSnapshot, Severity

from mas_msp_core.notifier import NotifierTransportAgent

from .service import AlertPolicyService, HostSnapshotEvaluation, build_synthetic_alert
from .store import (
    AlertConditionPhase,
    AlertConditionRecord,
    AlertConditionStore,
    AppliedAlertPolicyStore,
)


@dataclass(frozen=True, slots=True)
class HostSnapshotAlertOutcome:
    """Authoritative host snapshot plus the surfaced alerts it produced."""

    authoritative_snapshot: HealthSnapshot
    surfaced_alerts: list[AlertRaised]


class AlertPolicyAgent:
    """Own applied-policy alerting decisions after inventory binding."""

    def __init__(
        self,
        *,
        applied_policy_store: AppliedAlertPolicyStore,
        condition_store: AlertConditionStore,
        notifier: NotifierTransportAgent,
        service: AlertPolicyService | None = None,
    ) -> None:
        self._applied_policy_store = applied_policy_store
        self._condition_store = condition_store
        self._notifier = notifier
        self._service = service or AlertPolicyService()

    async def process_host_snapshot(
        self,
        snapshot: HealthSnapshot,
    ) -> HostSnapshotAlertOutcome:
        configuration = self._service.applied_or_default(
            await self._applied_policy_store.get_applied_policy(snapshot.client_id),
            client_id=snapshot.client_id,
        )
        evaluation = self._service.evaluate_host_snapshot(
            snapshot,
            configuration=configuration,
        )
        surfaced_alerts = await self._surface_host_candidates(evaluation)
        return HostSnapshotAlertOutcome(
            authoritative_snapshot=evaluation.authoritative_snapshot,
            surfaced_alerts=surfaced_alerts,
        )

    async def process_source_alert(
        self,
        alert: AlertRaised,
    ) -> list[AlertRaised]:
        configuration = self._service.applied_or_default(
            await self._applied_policy_store.get_applied_policy(alert.client_id),
            client_id=alert.client_id,
        )
        candidate = self._service.source_alert_candidate(
            alert,
            configuration=configuration,
        )
        record = await self._condition_store.get_condition(
            client_id=alert.client_id,
            asset_id=alert.asset.asset_id,
            condition_key=candidate.condition_key,
        )
        if (
            record is not None
            and record.last_surfaced_at is not None
            and (alert.occurred_at - record.last_surfaced_at).total_seconds()
            < candidate.cooldown_seconds
            and _severity_rank(alert.severity) <= _severity_rank(record.severity)
        ):
            await self._condition_store.put_condition(
                AlertConditionRecord(
                    client_id=record.client_id,
                    asset_id=record.asset_id,
                    condition_key=record.condition_key,
                    correlation_key=record.correlation_key,
                    category=record.category,
                    title=record.title,
                    severity=record.severity,
                    source_kind=record.source_kind,
                    normalized_facts=dict(record.normalized_facts),
                    phase=record.phase,
                    open_observation_count=record.open_observation_count,
                    close_observation_count=record.close_observation_count,
                    open_after_polls=record.open_after_polls,
                    close_after_polls=record.close_after_polls,
                    cooldown_seconds=record.cooldown_seconds,
                    opened_at=record.opened_at,
                    resolved_at=record.resolved_at,
                    last_observed_at=alert.occurred_at,
                    last_surfaced_at=record.last_surfaced_at,
                )
            )
            return []
        surfaced_alert = alert.model_copy(
            update={
                "normalized_facts": {
                    **dict(alert.normalized_facts),
                    "condition_key": candidate.condition_key,
                    "correlation_key": candidate.correlation_key,
                }
            }
        )
        await self._notifier.open_or_update_incident_from_alert(
            surfaced_alert,
            correlation_key=candidate.correlation_key,
        )
        await self._condition_store.put_condition(
            AlertConditionRecord(
                client_id=alert.client_id,
                asset_id=alert.asset.asset_id,
                condition_key=candidate.condition_key,
                correlation_key=candidate.correlation_key,
                category=alert.category,
                title=alert.title,
                severity=alert.severity,
                source_kind=alert.source_kind,
                normalized_facts=dict(surfaced_alert.normalized_facts),
                phase=AlertConditionPhase.OPEN,
                open_observation_count=1,
                close_observation_count=0,
                open_after_polls=candidate.open_after_polls,
                close_after_polls=candidate.close_after_polls,
                cooldown_seconds=candidate.cooldown_seconds,
                opened_at=alert.occurred_at if record is None else record.opened_at,
                resolved_at=None,
                last_observed_at=alert.occurred_at,
                last_surfaced_at=alert.occurred_at,
            )
        )
        return [surfaced_alert]

    async def _surface_host_candidates(
        self,
        evaluation: HostSnapshotEvaluation,
    ) -> list[AlertRaised]:
        client_id = evaluation.authoritative_snapshot.client_id
        asset_id = evaluation.authoritative_snapshot.asset.asset_id
        existing = {
            record.condition_key: record
            for record in await self._condition_store.list_conditions_for_asset(
                client_id=client_id,
                asset_id=asset_id,
            )
        }
        surfaced: list[AlertRaised] = []
        observed_at = evaluation.authoritative_snapshot.collected_at
        active_keys = {candidate.condition_key for candidate in evaluation.active_candidates}
        for record in existing.values():
            if record.condition_key in active_keys:
                continue
            if record.phase is AlertConditionPhase.OPEN:
                close_count = record.close_observation_count + 1
                if close_count >= record.close_after_polls:
                    await self._close_condition(
                        record=record,
                        snapshot=evaluation.authoritative_snapshot,
                    )
                    continue
                await self._condition_store.put_condition(
                    AlertConditionRecord(
                        client_id=record.client_id,
                        asset_id=record.asset_id,
                        condition_key=record.condition_key,
                        correlation_key=record.correlation_key,
                        category=record.category,
                        title=record.title,
                        severity=record.severity,
                        source_kind=record.source_kind,
                        normalized_facts=dict(record.normalized_facts),
                        phase=record.phase,
                        open_observation_count=record.open_observation_count,
                        close_observation_count=close_count,
                        open_after_polls=record.open_after_polls,
                        close_after_polls=record.close_after_polls,
                        cooldown_seconds=record.cooldown_seconds,
                        opened_at=record.opened_at,
                        resolved_at=record.resolved_at,
                        last_observed_at=observed_at,
                        last_surfaced_at=record.last_surfaced_at,
                    )
                )
                continue
            if record.phase is AlertConditionPhase.PENDING:
                await self._condition_store.delete_condition(
                    client_id=record.client_id,
                    asset_id=record.asset_id,
                    condition_key=record.condition_key,
                )
                continue
            if _cooldown_expired(record, observed_at=observed_at):
                await self._condition_store.delete_condition(
                    client_id=record.client_id,
                    asset_id=record.asset_id,
                    condition_key=record.condition_key,
                )
                continue
            await self._condition_store.put_condition(
                AlertConditionRecord(
                    client_id=record.client_id,
                    asset_id=record.asset_id,
                    condition_key=record.condition_key,
                    correlation_key=record.correlation_key,
                    category=record.category,
                    title=record.title,
                    severity=record.severity,
                    source_kind=record.source_kind,
                    normalized_facts=dict(record.normalized_facts),
                    phase=record.phase,
                    open_observation_count=0,
                    close_observation_count=0,
                    open_after_polls=record.open_after_polls,
                    close_after_polls=record.close_after_polls,
                    cooldown_seconds=record.cooldown_seconds,
                    opened_at=record.opened_at,
                    resolved_at=record.resolved_at,
                    last_observed_at=observed_at,
                    last_surfaced_at=record.last_surfaced_at,
                )
            )
        for candidate in evaluation.active_candidates:
            record = existing.get(candidate.condition_key)
            if record is None:
                record = AlertConditionRecord(
                    client_id=client_id,
                    asset_id=asset_id,
                    condition_key=candidate.condition_key,
                    correlation_key=candidate.correlation_key,
                    category=candidate.category,
                    title=candidate.title,
                    severity=candidate.severity,
                    source_kind=candidate.source_kind,
                    normalized_facts=dict(candidate.normalized_facts),
                    phase=AlertConditionPhase.PENDING,
                    open_observation_count=0,
                    close_observation_count=0,
                    open_after_polls=candidate.open_after_polls,
                    close_after_polls=candidate.close_after_polls,
                    cooldown_seconds=candidate.cooldown_seconds,
                    opened_at=None,
                    resolved_at=None,
                    last_observed_at=observed_at,
                    last_surfaced_at=None,
                )
            open_count = record.open_observation_count + 1
            should_surface = False
            next_phase = record.phase
            opened_at = record.opened_at
            last_surfaced_at = record.last_surfaced_at
            resolved_at = record.resolved_at
            if record.phase is AlertConditionPhase.OPEN:
                open_count = max(open_count, candidate.open_after_polls)
                next_phase = AlertConditionPhase.OPEN
                if _severity_rank(candidate.severity) > _severity_rank(record.severity):
                    should_surface = True
            elif record.phase is AlertConditionPhase.CLOSED:
                next_phase = AlertConditionPhase.CLOSED
                if open_count >= candidate.open_after_polls and not _cooldown_active(
                    record,
                    observed_at=observed_at,
                ):
                    next_phase = AlertConditionPhase.OPEN
                    opened_at = observed_at
                    resolved_at = None
                    should_surface = True
            elif open_count >= candidate.open_after_polls:
                next_phase = AlertConditionPhase.OPEN
                opened_at = observed_at
                should_surface = True
            updated_record = AlertConditionRecord(
                client_id=record.client_id,
                asset_id=record.asset_id,
                condition_key=record.condition_key,
                correlation_key=record.correlation_key,
                category=candidate.category,
                title=candidate.title,
                severity=candidate.severity,
                source_kind=candidate.source_kind,
                normalized_facts=dict(candidate.normalized_facts),
                phase=next_phase,
                open_observation_count=open_count,
                close_observation_count=0,
                open_after_polls=candidate.open_after_polls,
                close_after_polls=candidate.close_after_polls,
                cooldown_seconds=candidate.cooldown_seconds,
                opened_at=opened_at,
                resolved_at=resolved_at,
                last_observed_at=observed_at,
                last_surfaced_at=observed_at if should_surface else last_surfaced_at,
            )
            await self._condition_store.put_condition(updated_record)
            if not should_surface:
                continue
            surfaced_alert = build_synthetic_alert(
                candidate,
                alert_id=str(uuid4()),
                occurred_at=observed_at,
            )
            await self._notifier.open_or_update_incident_from_alert(
                surfaced_alert,
                correlation_key=candidate.correlation_key,
            )
            surfaced.append(surfaced_alert)
        return surfaced

    async def _close_condition(
        self,
        *,
        record: AlertConditionRecord,
        snapshot: HealthSnapshot,
    ) -> None:
        resolved_record = AlertConditionRecord(
            client_id=record.client_id,
            asset_id=record.asset_id,
            condition_key=record.condition_key,
            correlation_key=record.correlation_key,
            category=record.category,
            title=record.title,
            severity=record.severity,
            source_kind=record.source_kind,
            normalized_facts=dict(record.normalized_facts),
            phase=AlertConditionPhase.CLOSED,
            open_observation_count=0,
            close_observation_count=0,
            open_after_polls=record.open_after_polls,
            close_after_polls=record.close_after_polls,
            cooldown_seconds=record.cooldown_seconds,
            opened_at=record.opened_at,
            resolved_at=snapshot.collected_at,
            last_observed_at=snapshot.collected_at,
            last_surfaced_at=record.last_surfaced_at,
        )
        await self._condition_store.put_condition(resolved_record)
        await self._notifier.resolve_incident_for_condition(
            alert=AlertRaised(
                alert_id=str(uuid4()),
                client_id=record.client_id,
                fabric_id=snapshot.fabric_id,
                asset=snapshot.asset,
                source_kind=record.source_kind,
                occurred_at=snapshot.collected_at,
                severity=record.severity,
                category=record.category,
                title=record.title,
                normalized_facts={
                    **dict(record.normalized_facts),
                    "condition_key": record.condition_key,
                    "correlation_key": record.correlation_key,
                },
            ),
            correlation_key=record.correlation_key,
            resolved_at=snapshot.collected_at,
        )


def _cooldown_active(
    record: AlertConditionRecord,
    *,
    observed_at,
) -> bool:
    if record.resolved_at is None:
        return False
    return (observed_at - record.resolved_at).total_seconds() < record.cooldown_seconds


def _cooldown_expired(
    record: AlertConditionRecord,
    *,
    observed_at,
) -> bool:
    if record.resolved_at is None:
        return False
    return (observed_at - record.resolved_at).total_seconds() >= record.cooldown_seconds


def _severity_rank(severity: Severity) -> int:
    if severity is Severity.CRITICAL:
        return 4
    if severity is Severity.MAJOR:
        return 3
    if severity is Severity.MINOR:
        return 2
    if severity is Severity.WARNING:
        return 1
    return 0


__all__ = ["AlertPolicyAgent", "HostSnapshotAlertOutcome"]
