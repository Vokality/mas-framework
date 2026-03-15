"""phase_2_network_visibility"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from mas_ops_api.db.types import UtcDateTime

revision = "8f3f4f2db25c"
down_revision = "3a5523821094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_activity_events",
        sa.Column("asset_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_portfolio_activity_events_asset_id"),
        "portfolio_activity_events",
        ["asset_id"],
        unique=False,
    )
    op.add_column(
        "portfolio_assets",
        sa.Column("health_observed_at", UtcDateTime(), nullable=True),
    )
    op.add_column(
        "portfolio_assets",
        sa.Column("last_alert_at", UtcDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_assets", "last_alert_at")
    op.drop_column("portfolio_assets", "health_observed_at")
    op.drop_index(
        op.f("ix_portfolio_activity_events_asset_id"),
        table_name="portfolio_activity_events",
    )
    op.drop_column("portfolio_activity_events", "asset_id")
