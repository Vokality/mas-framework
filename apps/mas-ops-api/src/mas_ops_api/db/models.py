"""SQLAlchemy models for the ops-plane datastore."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mas_msp_contracts import (
    ApprovalState,
    AssetKind,
    ChatScope,
    ChatTurnState,
    ConfigApplyState,
    HealthState,
    IncidentState,
    Severity,
)

from mas_ops_api.auth.types import UserRole
from mas_ops_api.db.base import Base, utc_now
from mas_ops_api.db.types import UtcDateTime


class OpsUser(Base):
    """Human user account for the ops plane."""

    __tablename__ = "ops_users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.VIEWER.value)
    created_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class OpsUserPassword(Base):
    """Server-side password hash for an ops user."""

    __tablename__ = "ops_user_passwords"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ops_users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(Text())


class OpsSession(Base):
    """Server-side session record keyed by an opaque token hash."""

    __tablename__ = "ops_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ops_users.user_id", ondelete="CASCADE"),
        index=True,
    )
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    last_seen_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    idle_expires_at: Mapped[Any] = mapped_column(UtcDateTime())
    absolute_expires_at: Mapped[Any] = mapped_column(UtcDateTime())
    revoked_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)


class OpsUserClientAccess(Base):
    """Client grants for non-admin human users."""

    __tablename__ = "ops_user_client_access"
    __table_args__ = (UniqueConstraint("user_id", "client_id"),)

    access_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ops_users.user_id", ondelete="CASCADE"),
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(36), index=True)


class PortfolioClient(Base):
    """Human-facing client summary projection."""

    __tablename__ = "portfolio_clients"

    client_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fabric_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    open_alert_count: Mapped[int] = mapped_column(Integer, default=0)
    critical_asset_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class PortfolioIncident(Base):
    """Human-facing incident projection."""

    __tablename__ = "portfolio_incidents"

    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    fabric_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(32), default=IncidentState.OPEN.value)
    severity: Mapped[str] = mapped_column(String(32), default=Severity.INFO.value)
    summary: Mapped[str] = mapped_column(Text())
    recommended_actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    opened_at: Mapped[Any] = mapped_column(UtcDateTime())
    updated_at: Mapped[Any] = mapped_column(UtcDateTime())


class PortfolioIncidentAsset(Base):
    """Asset membership for one incident projection."""

    __tablename__ = "portfolio_incident_assets"
    __table_args__ = (
        Index("ix_portfolio_incident_assets_asset_id", "asset_id"),
        UniqueConstraint("incident_id", "asset_id"),
    )

    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolio_incidents.incident_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolio_assets.asset_id", ondelete="CASCADE"),
        primary_key=True,
    )


class IncidentEvidenceBundleRecord(Base):
    """Persisted evidence bundle for one incident."""

    __tablename__ = "incident_evidence_bundles"

    evidence_bundle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolio_incidents.incident_id", ondelete="CASCADE"),
        index=True,
    )
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolio_assets.asset_id", ondelete="CASCADE"),
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    fabric_id: Mapped[str] = mapped_column(String(36), index=True)
    collected_at: Mapped[Any] = mapped_column(UtcDateTime())
    summary: Mapped[str] = mapped_column(Text())
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class PortfolioAsset(Base):
    """Human-facing asset projection."""

    __tablename__ = "portfolio_assets"

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    fabric_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_kind: Mapped[str] = mapped_column(
        String(32),
        default=AssetKind.NETWORK_DEVICE.value,
    )
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mgmt_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    health_state: Mapped[str] = mapped_column(
        String(32), default=HealthState.UNKNOWN.value
    )
    health_observed_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    last_alert_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    updated_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class PortfolioActivityEvent(Base):
    """Activity timeline event projection."""

    __tablename__ = "portfolio_activity_events"
    __table_args__ = (
        UniqueConstraint("client_id", "fabric_id", "source_event_id"),
        Index("ix_portfolio_activity_events_incident_id", "incident_id"),
    )

    activity_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    source_event_id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    fabric_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class ChatSession(Base):
    """Persisted global or incident-scoped chat session."""

    __tablename__ = "chat_sessions"

    chat_session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), default=ChatScope.GLOBAL.value)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    fabric_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    incident_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class ChatTurn(Base):
    """Persisted operator turn inside a chat session."""

    __tablename__ = "chat_turns"

    turn_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"),
        index=True,
    )
    actor_user_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(32), default=ChatTurnState.RUNNING.value)
    submitted_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    completed_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ChatMessage(Base):
    """Persisted message inside a chat session."""

    __tablename__ = "chat_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"),
        index=True,
    )
    turn_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("chat_turns.turn_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text())
    created_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class ApprovalRequestRecord(Base):
    """Persisted approval request visible to human operators."""

    __tablename__ = "approval_requests"

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    fabric_id: Mapped[str] = mapped_column(String(36))
    incident_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    state: Mapped[str] = mapped_column(String(32), default=ApprovalState.PENDING.value)
    action_kind: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    requested_at: Mapped[Any] = mapped_column(UtcDateTime())
    expires_at: Mapped[Any] = mapped_column(UtcDateTime())
    requested_by_agent: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_summary: Mapped[str] = mapped_column(Text())
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    decided_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    approved_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    rejected_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    expired_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    cancelled_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    executed_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)


class ConfigDesiredStateRecord(Base):
    """Latest desired-state document for one client."""

    __tablename__ = "config_desired_states"

    client_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fabric_id: Mapped[str] = mapped_column(String(36))
    desired_state_version: Mapped[int] = mapped_column(Integer)
    tenant_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inventory_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    notification_routes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    updated_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class ConfigValidationRun(Base):
    """Config validation run record."""

    __tablename__ = "config_validation_runs"

    config_apply_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    desired_state_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    requested_by_user_id: Mapped[str] = mapped_column(String(36))
    requested_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    validated_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class ConfigApplyRun(Base):
    """Config apply run record."""

    __tablename__ = "config_apply_runs"

    config_apply_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    desired_state_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), default=ConfigApplyState.PENDING.value
    )
    requested_by_user_id: Mapped[str] = mapped_column(String(36))
    requested_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    approval_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("approval_requests.approval_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    completed_at: Mapped[Any | None] = mapped_column(UtcDateTime(), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)


class ConfigApplyStepRecord(Base):
    """Ordered step-level progress for one config apply run."""

    __tablename__ = "config_apply_steps"
    __table_args__ = (
        UniqueConstraint("config_apply_run_id", "step_index"),
        Index("ix_config_apply_steps_run_order", "config_apply_run_id", "step_index"),
    )

    config_apply_step_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    config_apply_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("config_apply_runs.config_apply_run_id", ondelete="CASCADE"),
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    step_name: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class OpsAuditEntry(Base):
    """Durable operator-visible audit entry for writes and config transitions."""

    __tablename__ = "ops_audit_entries"
    __table_args__ = (
        Index("ix_ops_audit_entries_client_id_occurred_at", "client_id", "occurred_at"),
        Index(
            "ix_ops_audit_entries_incident_id_occurred_at",
            "incident_id",
            "occurred_at",
        ),
    )

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    config_apply_run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)


class OpsStreamEvent(Base):
    """Committed SSE event log used for replay and live fan-out."""

    __tablename__ = "ops_stream_events"
    __table_args__ = (
        Index("ix_ops_stream_events_client_id_stream_id", "client_id", "stream_id"),
        Index("ix_ops_stream_events_incident_id_stream_id", "incident_id", "stream_id"),
        Index(
            "ix_ops_stream_events_chat_session_id_stream_id",
            "chat_session_id",
            "stream_id",
        ),
    )

    stream_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    event_name: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[Any] = mapped_column(UtcDateTime(), default=utc_now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


__all__ = [
    "ApprovalRequestRecord",
    "ChatMessage",
    "ChatSession",
    "ChatTurn",
    "ConfigApplyStepRecord",
    "ConfigApplyRun",
    "ConfigDesiredStateRecord",
    "ConfigValidationRun",
    "OpsAuditEntry",
    "OpsSession",
    "OpsStreamEvent",
    "OpsUser",
    "OpsUserClientAccess",
    "OpsUserPassword",
    "PortfolioIncidentAsset",
    "PortfolioActivityEvent",
    "PortfolioAsset",
    "PortfolioClient",
    "IncidentEvidenceBundleRecord",
    "PortfolioIncident",
]
