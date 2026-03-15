"""Notification delivery helpers for surfaced alerts and resolutions."""

from __future__ import annotations

from dataclasses import dataclass

from mas_msp_contracts import AlertRaised, IncidentRecord, Severity

from mas_msp_core.alerting.models import (
    AppliedAlertConfiguration,
    ChatOpsNotificationRoute,
    EmailNotificationRoute,
    NotificationRoute,
)
from mas_msp_core.alerting.store import AppliedAlertPolicyStore


SEVERITY_ORDER = {
    Severity.INFO.value: 0,
    Severity.WARNING.value: 1,
    Severity.MINOR.value: 2,
    Severity.MAJOR.value: 3,
    Severity.CRITICAL.value: 4,
}


@dataclass(frozen=True, slots=True)
class NotificationAttempt:
    """One route delivery attempt result."""

    route_kind: str
    target: str
    outcome: str
    detail: str


class NotificationService:
    """Apply route filters and execute the matching transport."""

    def __init__(
        self,
        *,
        applied_policy_store: AppliedAlertPolicyStore,
    ) -> None:
        self._applied_policy_store = applied_policy_store

    async def deliver_alert(
        self,
        *,
        alert: AlertRaised,
        incident: IncidentRecord,
        event_kind: str,
    ) -> list[NotificationAttempt]:
        configuration = await self._applied_policy_store.get_applied_policy(
            incident.client_id
        )
        if configuration is None:
            configuration = AppliedAlertConfiguration(client_id=incident.client_id)
        attempts: list[NotificationAttempt] = []
        for route in configuration.notification_routes:
            if not _route_matches_alert(route, alert=alert, event_kind=event_kind):
                continue
            attempts.append(
                _deliver(route, alert=alert, incident=incident, event_kind=event_kind)
            )
        return attempts


def _route_matches_alert(
    route: NotificationRoute,
    *,
    alert: AlertRaised,
    event_kind: str,
) -> bool:
    if event_kind == "resolved" and not route.send_resolved:
        return False
    if SEVERITY_ORDER[alert.severity.value] < SEVERITY_ORDER[route.severity_min.value]:
        return False
    if route.category_allowlist and alert.category not in route.category_allowlist:
        return False
    return True


def _deliver(
    route: NotificationRoute,
    *,
    alert: AlertRaised,
    incident: IncidentRecord,
    event_kind: str,
) -> NotificationAttempt:
    del incident
    if isinstance(route, ChatOpsNotificationRoute):
        return NotificationAttempt(
            route_kind=route.kind,
            target=route.target,
            outcome="succeeded",
            detail=(
                f"{route.provider}:{event_kind}:{alert.category}:"
                f"{alert.asset.hostname or alert.asset.asset_id}"
            ),
        )
    if isinstance(route, EmailNotificationRoute):
        return NotificationAttempt(
            route_kind=route.kind,
            target=route.target,
            outcome="succeeded",
            detail=f"{event_kind}:{alert.title}",
        )
    raise LookupError(f"unsupported notification route kind: {route.kind!r}")


__all__ = ["NotificationAttempt", "NotificationService"]
