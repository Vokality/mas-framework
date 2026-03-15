"""Pydantic request and response models for the ops-plane API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mas_msp_contracts import (
    ApprovalDecisionValue,
    ApprovalState,
    ChatScope,
    ChatTurnState,
)
from mas_ops_api.auth.types import UserRole


class ApiModel(BaseModel):
    """Base API model that accepts ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class SessionResponse(ApiModel):
    """Session restore and login response."""

    user_id: str
    email: str
    display_name: str
    role: UserRole
    client_ids: list[str]

    @classmethod
    def from_authenticated_user(cls, user) -> "SessionResponse":  # noqa: ANN001
        """Build a response model from the auth dataclass."""

        return cls(
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            client_ids=sorted(user.allowed_client_ids),
        )


class LoginRequest(BaseModel):
    """Login request body."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class ClientSummaryResponse(ApiModel):
    """Client summary read-model response."""

    client_id: str
    fabric_id: str
    name: str
    open_alert_count: int
    critical_asset_count: int
    updated_at: datetime


class IncidentResponse(ApiModel):
    """Incident detail response."""

    incident_id: str
    client_id: str
    fabric_id: str
    state: str
    severity: str
    summary: str
    recommended_actions: list[dict[str, Any]] = Field(default_factory=list)
    opened_at: datetime
    updated_at: datetime


class EvidenceBundleResponse(ApiModel):
    """Evidence bundle visible in the incident cockpit."""

    evidence_bundle_id: str
    incident_id: str
    asset_id: str
    client_id: str
    fabric_id: str
    collected_at: datetime
    summary: str
    items: list[dict[str, Any]]


class IncidentDetailResponse(IncidentResponse):
    """Expanded incident detail for the cockpit."""

    assets: list["AssetResponse"] = Field(default_factory=list)
    evidence_bundles: list[EvidenceBundleResponse] = Field(default_factory=list)


class AssetResponse(ApiModel):
    """Asset detail response."""

    asset_id: str
    client_id: str
    fabric_id: str
    asset_kind: str
    vendor: str | None
    model: str | None
    hostname: str | None
    mgmt_address: str | None
    site: str | None
    tags: list[str]
    health_state: str
    health_observed_at: datetime | None
    last_alert_at: datetime | None
    updated_at: datetime


class ActivityEventResponse(ApiModel):
    """Incident activity timeline entry."""

    activity_id: int
    source_event_id: str
    client_id: str
    fabric_id: str
    incident_id: str | None
    asset_id: str | None
    event_type: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any]
    occurred_at: datetime


class ChatSessionCreateRequest(BaseModel):
    """Create-chat-session request body."""

    scope: ChatScope
    client_id: str | None = None
    fabric_id: str | None = None
    incident_id: str | None = None


class ChatMessageCreateRequest(BaseModel):
    """Append-message request body."""

    message: str = Field(min_length=1)


class ChatTurnResponse(ApiModel):
    """Persisted chat turn response."""

    turn_id: str
    request_id: str
    chat_session_id: str
    actor_user_id: str
    state: ChatTurnState
    submitted_at: datetime
    completed_at: datetime | None
    approval_id: str | None


class ChatMessageResponse(ApiModel):
    """Persisted chat message response."""

    message_id: str
    chat_session_id: str
    turn_id: str | None
    role: str
    content: str
    created_at: datetime


class ChatSessionResponse(ApiModel):
    """Chat session with turns and messages."""

    chat_session_id: str
    scope: ChatScope
    client_id: str | None
    fabric_id: str | None
    incident_id: str | None
    created_by_user_id: str
    created_at: datetime
    turns: list[ChatTurnResponse]
    messages: list[ChatMessageResponse]


class ApprovalResponse(ApiModel):
    """Approval request response."""

    approval_id: str
    client_id: str
    fabric_id: str
    incident_id: str | None
    state: ApprovalState
    action_kind: str
    title: str
    requested_at: datetime
    expires_at: datetime
    requested_by_agent: str
    payload: dict[str, Any]
    risk_summary: str
    decided_by_user_id: str | None
    decision_reason: str | None
    decided_at: datetime | None
    approved_at: datetime | None
    rejected_at: datetime | None
    expired_at: datetime | None
    cancelled_at: datetime | None
    executed_at: datetime | None


class ApprovalDecisionRequest(BaseModel):
    """Approval-decision request body."""

    decision: ApprovalDecisionValue
    reason: str | None = None


class ConfigApplyRequestResponse(ApiModel):
    """Config apply-run response."""

    config_apply_run_id: str
    client_id: str
    desired_state_version: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_summary: str | None


IncidentDetailResponse.model_rebuild()


__all__ = [
    "ActivityEventResponse",
    "ApprovalDecisionRequest",
    "ApprovalResponse",
    "AssetResponse",
    "ChatMessageCreateRequest",
    "ChatMessageResponse",
    "ChatSessionCreateRequest",
    "ChatSessionResponse",
    "ChatTurnResponse",
    "ClientSummaryResponse",
    "ConfigApplyRequestResponse",
    "EvidenceBundleResponse",
    "IncidentDetailResponse",
    "IncidentResponse",
    "LoginRequest",
    "SessionResponse",
]
