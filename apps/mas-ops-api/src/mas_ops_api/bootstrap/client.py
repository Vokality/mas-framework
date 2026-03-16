"""Idempotent managed-client enrollment bootstrap for local environments."""

from __future__ import annotations

from dataclasses import dataclass, field

from mas_msp_contracts import ConfigDesiredState
from mas_msp_core import AppliedAlertConfiguration
from mas_ops_api.audit import AuditService
from mas_ops_api.clients import ClientEnrollmentInput, ClientEnrollmentService
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService
from mas_ops_api.visibility import OpsPlaneAppliedAlertPolicyStore


@dataclass(frozen=True, slots=True)
class ClientBootstrapConfig:
    """Bootstrap inputs for an enrolled managed client."""

    client_id: str
    fabric_id: str
    display_name: str
    initial_policy: dict[str, object] = field(default_factory=dict)
    materialize_initial_alert_policy: bool = False


async def ensure_client_enrollment(
    settings: OpsApiSettings,
    *,
    config: ClientBootstrapConfig,
) -> None:
    """Ensure one managed client is explicitly enrolled in the ops plane."""

    database = Database(settings)
    service = ClientEnrollmentService(
        audit_service=AuditService(),
        stream_service=StreamService(settings),
    )
    applied_policy_store = OpsPlaneAppliedAlertPolicyStore(database=database)
    try:
        async with database.session_factory() as session:
            result = await service.ensure_enrolled(
                session,
                ClientEnrollmentInput(
                    client_id=config.client_id,
                    fabric_id=config.fabric_id,
                    display_name=config.display_name,
                    initial_policy=config.initial_policy,
                ),
                actor_type="system",
                actor_id="ops-bootstrap",
            )
        if config.materialize_initial_alert_policy and config.initial_policy:
            existing = await applied_policy_store.get_applied_policy(config.client_id)
            if (
                result.desired_state_created
                or result.desired_state_changed
                or existing is None
            ):
                desired_state = ConfigDesiredState.model_validate(
                    result.desired_state,
                    from_attributes=True,
                )
                await applied_policy_store.apply_configuration(
                    AppliedAlertConfiguration.from_desired_state(desired_state)
                )
    finally:
        await database.dispose()


def build_dogfood_initial_policy(*, hostname: str) -> dict[str, object]:
    """Return the default dogfood alert policy for the local MAS runtime host."""

    return {
        "alerting": {
            "overrides": [
                {
                    "match": {"hostname": hostname},
                    "host": {"services": {"watch": [hostname]}},
                }
            ]
        }
    }


__all__ = [
    "ClientBootstrapConfig",
    "build_dogfood_initial_policy",
    "ensure_client_enrollment",
]
