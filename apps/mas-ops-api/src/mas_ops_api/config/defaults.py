"""Default desired-state records for newly enrolled clients."""

from __future__ import annotations

from datetime import datetime

from mas_ops_api.db.models import ConfigDesiredStateRecord


def build_initial_desired_state_record(
    *,
    client_id: str,
    fabric_id: str,
    display_name: str,
    updated_at: datetime,
) -> ConfigDesiredStateRecord:
    """Return the first desired-state document for an enrolled client."""

    return ConfigDesiredStateRecord(
        client_id=client_id,
        fabric_id=fabric_id,
        desired_state_version=1,
        tenant_metadata={"display_name": display_name},
        policy={},
        inventory_sources=[],
        notification_routes=[],
        updated_at=updated_at,
    )


__all__ = ["build_initial_desired_state_record"]
