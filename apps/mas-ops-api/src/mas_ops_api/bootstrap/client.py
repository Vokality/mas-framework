"""Idempotent managed-client enrollment bootstrap for local environments."""

from __future__ import annotations

from dataclasses import dataclass

from mas_ops_api.audit import AuditService
from mas_ops_api.clients import ClientEnrollmentInput, ClientEnrollmentService
from mas_ops_api.db.session import Database
from mas_ops_api.settings import OpsApiSettings
from mas_ops_api.streams.service import StreamService


@dataclass(frozen=True, slots=True)
class ClientBootstrapConfig:
    """Bootstrap inputs for an enrolled managed client."""

    client_id: str
    fabric_id: str
    display_name: str


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
    try:
        async with database.session_factory() as session:
            await service.ensure_enrolled(
                session,
                ClientEnrollmentInput(
                    client_id=config.client_id,
                    fabric_id=config.fabric_id,
                    display_name=config.display_name,
                ),
                actor_type="system",
                actor_id="ops-bootstrap",
            )
    finally:
        await database.dispose()


__all__ = ["ClientBootstrapConfig", "ensure_client_enrollment"]
