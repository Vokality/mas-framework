"""Operator-visible audit trail helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mas_ops_api.db.models import OpsAuditEntry


@dataclass(frozen=True, slots=True)
class AuditEntryInput:
    """Immutable input for one persisted audit entry."""

    client_id: str
    actor_type: str
    actor_id: str
    target_type: str
    target_id: str
    action: str
    outcome: str
    incident_id: str | None = None
    approval_id: str | None = None
    config_apply_run_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)
    occurred_at: datetime | None = None


class AuditService:
    """Persist audit entries inside the current database transaction."""

    async def append_entry(
        self,
        session: AsyncSession,
        payload: AuditEntryInput,
    ) -> OpsAuditEntry:
        """Append one audit entry to the active session."""

        entry = OpsAuditEntry(
            audit_id=str(uuid4()),
            client_id=payload.client_id,
            incident_id=payload.incident_id,
            approval_id=payload.approval_id,
            config_apply_run_id=payload.config_apply_run_id,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            action=payload.action,
            outcome=payload.outcome,
            details=dict(payload.details),
            occurred_at=(payload.occurred_at or datetime.now(UTC)).astimezone(UTC),
        )
        session.add(entry)
        await session.flush()
        return entry


__all__ = ["AuditEntryInput", "AuditService"]
