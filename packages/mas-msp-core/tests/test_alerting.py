from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mas_msp_contracts import (
    AlertRaised,
    AssetKind,
    AssetRef,
    ConfigDesiredState,
    HealthSnapshot,
    HealthState,
    IncidentRecord,
    IncidentState,
    Severity,
)
from mas_msp_core import (
    AlertPolicyAgent,
    AlertPolicyService,
    AppliedAlertConfiguration,
    InMemoryAlertConditionStore,
    InMemoryAppliedAlertPolicyStore,
)


CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FABRIC_ID = "22222222-2222-4222-8222-222222222222"


def _asset(
    *,
    asset_id: str = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    tags: list[str] | None = None,
) -> AssetRef:
    return AssetRef(
        asset_id=asset_id,
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset_kind=AssetKind.LINUX_HOST,
        vendor="Linux",
        model="Ubuntu 24.04",
        hostname="web-01",
        mgmt_address="10.20.0.15",
        site="nyc-1",
        tags=["linux", *(tags or [])],
    )


def _snapshot(
    *,
    services: list[dict[str, object]],
    cpu_percent: float = 41.0,
    health_state: HealthState = HealthState.UNKNOWN,
) -> HealthSnapshot:
    return HealthSnapshot(
        snapshot_id=str(uuid4()),
        client_id=CLIENT_ID,
        fabric_id=FABRIC_ID,
        asset=_asset(),
        source_kind="ssh_poll",
        collected_at=datetime(2026, 3, 15, 15, 0, tzinfo=UTC),
        health_state=health_state,
        metrics={
            "cpu_percent": cpu_percent,
            "memory_percent": 58.0,
            "disk_percent": 70.0,
            "services": services,
        },
        findings=[],
    )


@dataclass(slots=True)
class RecordingNotifier:
    opened_alerts: list[AlertRaised] = field(default_factory=list)
    resolved_alerts: list[AlertRaised] = field(default_factory=list)

    async def open_or_update_incident_from_alert(
        self,
        alert: AlertRaised,
        *,
        correlation_key: str | None = None,
    ) -> IncidentRecord:
        del correlation_key
        self.opened_alerts.append(alert)
        return IncidentRecord(
            incident_id=str(uuid4()),
            client_id=alert.client_id,
            fabric_id=alert.fabric_id,
            correlation_key=alert.normalized_facts.get("correlation_key")
            if isinstance(alert.normalized_facts.get("correlation_key"), str)
            else None,
            state=IncidentState.OPEN,
            severity=alert.severity,
            summary=alert.title,
            asset_ids=[alert.asset.asset_id],
            opened_at=alert.occurred_at,
            updated_at=alert.occurred_at,
        )

    async def resolve_incident_for_condition(
        self,
        *,
        alert: AlertRaised,
        correlation_key: str,
        resolved_at: datetime,
    ) -> IncidentRecord:
        del correlation_key
        self.resolved_alerts.append(alert)
        return IncidentRecord(
            incident_id=str(uuid4()),
            client_id=alert.client_id,
            fabric_id=alert.fabric_id,
            correlation_key=alert.normalized_facts.get("correlation_key")
            if isinstance(alert.normalized_facts.get("correlation_key"), str)
            else None,
            state=IncidentState.RESOLVED,
            severity=alert.severity,
            summary=alert.title,
            asset_ids=[alert.asset.asset_id],
            opened_at=resolved_at,
            updated_at=resolved_at,
        )


def test_applied_alert_configuration_prefers_asset_override_over_tags() -> None:
    configuration = AppliedAlertConfiguration.from_desired_state(
        ConfigDesiredState(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            desired_state_version=1,
            tenant_metadata={},
            policy={
                "alerting": {
                    "host_defaults": {
                        "metrics": {
                            "cpu_percent": {"warning": 70, "critical": 90},
                        },
                        "services": {"watch": ["sshd"]},
                    },
                    "overrides": [
                        {
                            "match": {"tags_any": ["production"]},
                            "host": {"services": {"watch": ["nginx"]}},
                        },
                        {
                            "match": {"asset_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
                            "host": {"services": {"watch": ["postgres"]}},
                        },
                    ],
                }
            },
            inventory_sources=[],
            notification_routes=[{"kind": "email", "target": "noc@example.com"}],
        )
    )

    policy = configuration.resolve_host_policy(_asset(tags=["production"]))

    assert policy.services.watch == ["postgres"]
    assert policy.metrics.cpu_percent.warning == 70


def test_applied_alert_configuration_rejects_invalid_threshold_order() -> None:
    with pytest.raises(ValidationError):
        AppliedAlertConfiguration.from_desired_state(
            ConfigDesiredState(
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                desired_state_version=1,
                tenant_metadata={},
                policy={
                    "alerting": {
                        "host_defaults": {
                            "metrics": {
                                "cpu_percent": {"warning": 95, "critical": 90}
                            }
                        }
                    }
                },
                inventory_sources=[],
                notification_routes=[{"kind": "email", "target": "noc@example.com"}],
            )
        )


def test_alert_policy_service_rewrites_host_snapshot_health_and_findings() -> None:
    service = AlertPolicyService()
    configuration = AppliedAlertConfiguration.from_desired_state(
        ConfigDesiredState(
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            desired_state_version=1,
            tenant_metadata={},
            policy={"alerting": {"host_defaults": {"services": {"watch": ["nginx"]}}}},
            inventory_sources=[],
            notification_routes=[],
        )
    )

    evaluation = service.evaluate_host_snapshot(
        _snapshot(
            cpu_percent=96.0,
            services=[
                {"service_name": "nginx", "service_state": "failed"},
                {"service_name": "sshd", "service_state": "running"},
            ],
        ),
        configuration=configuration,
    )

    assert evaluation.authoritative_snapshot.health_state is HealthState.CRITICAL
    assert {candidate.condition_key for candidate in evaluation.active_candidates} == {
        "metric:cpu_percent",
        "service:nginx",
    }
    assert {
        finding["code"] for finding in evaluation.authoritative_snapshot.findings
    } == {"cpu_percent_critical", "service_not_running"}


def test_alert_policy_service_ignores_service_states_when_watch_list_is_empty() -> None:
    service = AlertPolicyService()
    configuration = AppliedAlertConfiguration(client_id=CLIENT_ID)

    evaluation = service.evaluate_host_snapshot(
        _snapshot(
            services=[
                {"service_name": "nginx", "service_state": "failed"},
            ]
        ),
        configuration=configuration,
    )

    assert evaluation.authoritative_snapshot.health_state is HealthState.HEALTHY
    assert evaluation.active_candidates == []
    assert evaluation.authoritative_snapshot.findings == []


@pytest.mark.asyncio
async def test_alert_policy_agent_resolves_service_condition_after_clear() -> None:
    applied_policy_store = InMemoryAppliedAlertPolicyStore()
    await applied_policy_store.apply_configuration(
        AppliedAlertConfiguration.from_desired_state(
            ConfigDesiredState(
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                desired_state_version=1,
                tenant_metadata={},
                policy={
                    "alerting": {
                        "host_defaults": {
                            "services": {
                                "watch": ["nginx"],
                                "close_after_polls": 1,
                            }
                        }
                    }
                },
                inventory_sources=[],
                notification_routes=[],
            )
        )
    )
    notifier = RecordingNotifier()
    agent = AlertPolicyAgent(
        applied_policy_store=applied_policy_store,
        condition_store=InMemoryAlertConditionStore(),
        notifier=notifier,
    )

    opened = await agent.process_host_snapshot(
        _snapshot(
            services=[
                {"service_name": "nginx", "service_state": "failed"},
                {"service_name": "sshd", "service_state": "running"},
            ]
        )
    )
    resolved = await agent.process_host_snapshot(
        _snapshot(
            services=[
                {"service_name": "nginx", "service_state": "running"},
                {"service_name": "sshd", "service_state": "running"},
            ]
        )
    )

    assert len(opened.surfaced_alerts) == 1
    assert notifier.opened_alerts[0].category == "service"
    assert resolved.authoritative_snapshot.health_state is HealthState.HEALTHY
    assert len(notifier.resolved_alerts) == 1
    assert notifier.resolved_alerts[0].normalized_facts["condition_key"] == "service:nginx"


@pytest.mark.asyncio
async def test_alert_policy_agent_enforces_cooldown_before_reopen() -> None:
    applied_policy_store = InMemoryAppliedAlertPolicyStore()
    await applied_policy_store.apply_configuration(
        AppliedAlertConfiguration.from_desired_state(
            ConfigDesiredState(
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                desired_state_version=1,
                tenant_metadata={},
                policy={
                    "alerting": {
                        "host_defaults": {
                            "services": {
                                "watch": ["nginx"],
                                "open_after_polls": 1,
                                "close_after_polls": 1,
                                "cooldown_seconds": 300,
                            }
                        }
                    }
                },
                inventory_sources=[],
                notification_routes=[],
            )
        )
    )
    notifier = RecordingNotifier()
    agent = AlertPolicyAgent(
        applied_policy_store=applied_policy_store,
        condition_store=InMemoryAlertConditionStore(),
        notifier=notifier,
    )

    opened_at = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
    await agent.process_host_snapshot(
        _snapshot(
            services=[{"service_name": "nginx", "service_state": "failed"}]
        ).model_copy(update={"collected_at": opened_at})
    )
    await agent.process_host_snapshot(
        _snapshot(
            services=[{"service_name": "nginx", "service_state": "running"}]
        ).model_copy(update={"collected_at": opened_at.replace(minute=1)})
    )
    within_cooldown = await agent.process_host_snapshot(
        _snapshot(
            services=[{"service_name": "nginx", "service_state": "failed"}]
        ).model_copy(update={"collected_at": opened_at.replace(minute=2)})
    )
    after_cooldown = await agent.process_host_snapshot(
        _snapshot(
            services=[{"service_name": "nginx", "service_state": "failed"}]
        ).model_copy(update={"collected_at": opened_at.replace(minute=7)})
    )

    assert within_cooldown.surfaced_alerts == []
    assert len(notifier.opened_alerts) == 2
    assert len(after_cooldown.surfaced_alerts) == 1


@pytest.mark.asyncio
async def test_source_service_alert_dedupes_against_open_polled_condition() -> None:
    applied_policy_store = InMemoryAppliedAlertPolicyStore()
    await applied_policy_store.apply_configuration(
        AppliedAlertConfiguration.from_desired_state(
            ConfigDesiredState(
                client_id=CLIENT_ID,
                fabric_id=FABRIC_ID,
                desired_state_version=1,
                tenant_metadata={},
                policy={"alerting": {"host_defaults": {"services": {"watch": ["nginx"]}}}},
                inventory_sources=[],
                notification_routes=[],
            )
        )
    )
    notifier = RecordingNotifier()
    agent = AlertPolicyAgent(
        applied_policy_store=applied_policy_store,
        condition_store=InMemoryAlertConditionStore(),
        notifier=notifier,
    )
    await agent.process_host_snapshot(
        _snapshot(
            services=[
                {"service_name": "nginx", "service_state": "failed"},
                {"service_name": "sshd", "service_state": "running"},
            ]
        )
    )

    surfaced_alerts = await agent.process_source_alert(
        AlertRaised(
            alert_id=str(uuid4()),
            client_id=CLIENT_ID,
            fabric_id=FABRIC_ID,
            asset=_asset(),
            source_kind="journald",
            occurred_at=datetime(2026, 3, 15, 15, 0, 30, tzinfo=UTC),
            severity=Severity.MAJOR,
            category="service",
            title="nginx on web-01 entered failed state",
            normalized_facts={"service_name": "nginx", "message": "entered failed state"},
        )
    )

    assert surfaced_alerts == []
    assert len(notifier.opened_alerts) == 1
