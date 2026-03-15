"""phase_3_incident_cockpit"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from mas_ops_api.db.types import UtcDateTime

revision = "2c0c77d9c6a1"
down_revision = "8f3f4f2db25c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_incidents",
        sa.Column(
            "recommended_actions",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_table(
        "portfolio_incident_assets",
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["portfolio_assets.asset_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["portfolio_incidents.incident_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("incident_id", "asset_id"),
    )
    op.create_index(
        "ix_portfolio_incident_assets_asset_id",
        "portfolio_incident_assets",
        ["asset_id"],
        unique=False,
    )
    op.create_table(
        "incident_evidence_bundles",
        sa.Column("evidence_bundle_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("fabric_id", sa.String(length=36), nullable=False),
        sa.Column("collected_at", UtcDateTime(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["portfolio_assets.asset_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["portfolio_incidents.incident_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evidence_bundle_id"),
    )
    op.create_index(
        op.f("ix_incident_evidence_bundles_asset_id"),
        "incident_evidence_bundles",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incident_evidence_bundles_client_id"),
        "incident_evidence_bundles",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incident_evidence_bundles_fabric_id"),
        "incident_evidence_bundles",
        ["fabric_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incident_evidence_bundles_incident_id"),
        "incident_evidence_bundles",
        ["incident_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_incident_evidence_bundles_incident_id"),
        table_name="incident_evidence_bundles",
    )
    op.drop_index(
        op.f("ix_incident_evidence_bundles_fabric_id"),
        table_name="incident_evidence_bundles",
    )
    op.drop_index(
        op.f("ix_incident_evidence_bundles_client_id"),
        table_name="incident_evidence_bundles",
    )
    op.drop_index(
        op.f("ix_incident_evidence_bundles_asset_id"),
        table_name="incident_evidence_bundles",
    )
    op.drop_table("incident_evidence_bundles")
    op.drop_index(
        "ix_portfolio_incident_assets_asset_id",
        table_name="portfolio_incident_assets",
    )
    op.drop_table("portfolio_incident_assets")
    op.drop_column("portfolio_incidents", "recommended_actions")
