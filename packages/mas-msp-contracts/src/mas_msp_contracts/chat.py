"""Operator chat contracts."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from ._base import ContractModel
from .enums import ChatScope, ChatTurnState, IncidentState
from .types import (
    ApprovalId,
    AssetId,
    ChatSessionId,
    ClientId,
    EvidenceBundleId,
    FabricId,
    IncidentId,
    JSONObject,
    NonEmptyStr,
    RequestId,
    TurnId,
)


class OperatorChatRequest(ContractModel):
    """Operator message sent from the ops plane into a fabric."""

    request_id: RequestId
    chat_session_id: ChatSessionId
    turn_id: TurnId
    scope: ChatScope
    actor_user_id: NonEmptyStr
    allowed_client_ids: list[ClientId] = Field(min_length=1)
    client_id: ClientId | None = None
    fabric_id: FabricId | None = None
    incident_id: IncidentId | None = None
    asset_ids: list[AssetId] = Field(default_factory=list)
    message: NonEmptyStr

    @model_validator(mode="after")
    def validate_scope_requirements(self) -> Self:
        """Require incident context when the chat is incident-scoped."""

        if self.scope is ChatScope.INCIDENT:
            if self.client_id is None:
                raise ValueError("incident-scoped chat requires client_id")
            if self.fabric_id is None:
                raise ValueError("incident-scoped chat requires fabric_id")
            if self.incident_id is None:
                raise ValueError("incident-scoped chat requires incident_id")
        return self


class OperatorChatResponse(ContractModel):
    """Operator-facing response returned from fabric-local reasoning."""

    request_id: RequestId
    chat_session_id: ChatSessionId
    turn_id: TurnId
    state: ChatTurnState
    incident_id: IncidentId | None = None
    markdown_summary: NonEmptyStr
    evidence_bundle_ids: list[EvidenceBundleId] = Field(default_factory=list)
    approval_id: ApprovalId | None = None
    recommended_actions: list[JSONObject] = Field(default_factory=list)


class IncidentChatContext(ContractModel):
    """Immutable context passed into incident-scoped chat turns."""

    chat_session_id: ChatSessionId
    incident_id: IncidentId
    client_id: ClientId
    fabric_id: FabricId
    asset_ids: list[AssetId] = Field(default_factory=list)
    incident_state: IncidentState
    recent_evidence_bundle_ids: list[EvidenceBundleId] = Field(default_factory=list)


__all__ = [
    "IncidentChatContext",
    "OperatorChatRequest",
    "OperatorChatResponse",
]
