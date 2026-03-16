"""phase_5_alerting_persistence"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from mas_ops_api.db.types import UtcDateTime

revision = "0f8d8e9f5b42"
down_revision = "4f9a1d7d6b2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "applied_alert_policies",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.add_column(
        "portfolio_incidents",
        sa.Column("correlation_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("ix_portfolio_incidents_correlation_key"),
        "portfolio_incidents",
        ["correlation_key"],
        unique=False,
    )
    op.create_table(
        "alert_condition_states",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("condition_key", sa.String(length=255), nullable=False),
        sa.Column("correlation_key", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("normalized_facts", sa.JSON(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("open_observation_count", sa.Integer(), nullable=False),
        sa.Column("close_observation_count", sa.Integer(), nullable=False),
        sa.Column("open_after_polls", sa.Integer(), nullable=False),
        sa.Column("close_after_polls", sa.Integer(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("opened_at", UtcDateTime(), nullable=True),
        sa.Column("resolved_at", UtcDateTime(), nullable=True),
        sa.Column("last_observed_at", UtcDateTime(), nullable=False),
        sa.Column("last_surfaced_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("client_id", "asset_id", "condition_key"),
    )
    op.create_index(
        "ix_alert_condition_states_client_asset",
        "alert_condition_states",
        ["client_id", "asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_condition_states_correlation_key",
        "alert_condition_states",
        ["correlation_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_condition_states_correlation_key",
        table_name="alert_condition_states",
    )
    op.drop_index(
        "ix_alert_condition_states_client_asset",
        table_name="alert_condition_states",
    )
    op.drop_table("alert_condition_states")
    op.drop_index(
        op.f("ix_portfolio_incidents_correlation_key"),
        table_name="portfolio_incidents",
    )
    op.drop_column("portfolio_incidents", "correlation_key")
    op.drop_table("applied_alert_policies")
