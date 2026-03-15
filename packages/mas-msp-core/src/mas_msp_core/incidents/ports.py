"""Protocol boundaries for fabric-local incident orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mas_msp_contracts import (
    AlertRaised,
    AssetRef,
    EvidenceBundle,
    IncidentRecord,
    IncidentState,
    JSONObject,
    OperatorChatRequest,
    OperatorChatResponse,
    RemediationExecute,
    Severity,
)

from .models import IncidentRemediationExecution


class IncidentChatHandler(Protocol):
    """Route one incident-scoped operator chat request inside a client fabric."""

    async def handle_incident_chat_request(
        self,
        request: OperatorChatRequest,
    ) -> OperatorChatResponse: ...


class VisibilityAlertHandler(Protocol):
    """Handle one visibility alert that may need durable incident ownership."""

    async def handle_visibility_alert(self, alert: AlertRaised) -> IncidentRecord: ...


class IncidentRemediationHandler(Protocol):
    """Execute one approved incident remediation inside a client fabric."""

    async def execute_approved_remediation(
        self,
        *,
        approval_id: str,
        remediation: RemediationExecute,
    ) -> IncidentRemediationExecution: ...


class IncidentContextReader(Protocol):
    """Read the incident context needed by fabric-local orchestration."""

    async def find_active_incident(
        self,
        *,
        client_id: str,
        correlation_key: str | None,
        asset_id: str | None,
    ) -> IncidentRecord | None: ...

    async def get_incident(self, incident_id: str) -> IncidentRecord | None: ...

    async def list_assets_for_incident(self, incident_id: str) -> list[AssetRef]: ...

    async def list_activity_for_incident(
        self,
        incident_id: str,
    ) -> list[JSONObject]: ...

    async def list_evidence_for_incident(
        self,
        incident_id: str,
    ) -> list[EvidenceBundle]: ...


class NotifierTransport(Protocol):
    """Persist operator-visible incident state and timeline updates."""

    async def record_visibility_incident(
        self,
        *,
        incident_id: str | None,
        client_id: str,
        fabric_id: str,
        correlation_key: str | None,
        summary: str,
        severity: Severity,
        state: IncidentState,
        asset_ids: list[str],
        asset_refs: list[AssetRef],
        occurred_at: datetime,
        source: str,
        source_event_id: str,
    ) -> IncidentRecord: ...

    async def persist_summary(
        self,
        *,
        incident_id: str,
        summary: str,
        severity: Severity,
        state: IncidentState,
        recommended_actions: list[JSONObject] | None,
        asset_ids: list[str],
    ) -> IncidentRecord: ...

    async def append_activity(
        self,
        *,
        incident_id: str,
        event_type: str,
        payload: JSONObject,
        asset_id: str | None,
        occurred_at: datetime | None = None,
    ) -> None: ...

    async def persist_evidence_bundle(
        self,
        *,
        bundle: EvidenceBundle,
        client_id: str,
        fabric_id: str,
    ) -> None: ...


__all__ = [
    "IncidentChatHandler",
    "IncidentContextReader",
    "IncidentRemediationHandler",
    "NotifierTransport",
    "VisibilityAlertHandler",
]
